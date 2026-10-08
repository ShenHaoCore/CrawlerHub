"""任务调度入口的测试。

重点验证「任务之间异步运行、互不干扰」：

- 单任务异常 / 超时 / 内部取消，不影响其他任务；
- 停用与非法配置的任务被跳过；
- 任务结果顺序与配置声明顺序一致；
- 并发闸门、覆盖项过滤与摘要统计等调度行为。
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, Callable, Dict, List

import pytest
import yaml

from crawlerhub import __main__ as entry

Handler = Callable[[str, Dict[str, Any]], Any]


def write_tasks(tmp_path: Path, data: Dict[str, Any]) -> str:
    """把任务配置写入临时文件并返回路径。"""
    path = tmp_path / "tasks.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return str(path)


def install_execute(
    monkeypatch: pytest.MonkeyPatch,
    handlers: Dict[str, Handler],
) -> List[str]:
    """替换 ``_execute_platform`` 为按平台分发的假实现，返回调用序列。"""
    calls: List[str] = []

    async def _fake(
        platform: str,
        settings: Dict[str, Any],
        base_config_path: str,
    ) -> Dict[str, Any]:
        calls.append(platform)
        return await handlers[platform](platform, settings)

    monkeypatch.setattr(entry, "_execute_platform", _fake)
    return calls


async def ok_handler(platform: str, settings: Dict[str, Any]) -> Dict[str, Any]:
    """总是成功的假任务。"""
    return {"fetched": len(settings.get("uids") or [])}


async def boom_handler(platform: str, settings: Dict[str, Any]) -> Dict[str, Any]:
    """总是抛异常的假任务。"""
    raise RuntimeError(f"{platform} 炸了")


class TestTaskIsolation:
    """任务级隔离：一个任务出问题，其余任务照常完成。"""

    async def test_failure_does_not_break_others(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        install_execute(monkeypatch, {"a": ok_handler, "b": boom_handler, "c": ok_handler})
        task_file = write_tasks(
            tmp_path,
            {
                "platforms": {
                    "a": {"uids": ["1", "2"]},
                    "b": {"uids": ["3"]},
                    "c": {"uids": ["4"]},
                }
            },
        )

        results = await entry._run_all_tasks(task_file, "unused.yaml")

        by_platform = {r["platform"]: r for r in results}
        assert by_platform["a"]["status"] == "ok"
        assert by_platform["a"]["fetched"] == 2
        assert by_platform["b"]["status"] == "error"
        assert by_platform["b"]["fetched"] == 0
        assert "RuntimeError" in by_platform["b"]["error"]
        assert by_platform["c"]["status"] == "ok"

    async def test_all_failures_are_collected(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        install_execute(monkeypatch, {"a": boom_handler, "b": boom_handler})
        task_file = write_tasks(
            tmp_path, {"platforms": {"a": {"uids": ["1"]}, "b": {"uids": ["1"]}}}
        )

        results = await entry._run_all_tasks(task_file, "unused.yaml")

        assert [r["status"] for r in results] == ["error", "error"]

    async def test_timeout_kills_only_itself(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        async def slow(platform: str, settings: Dict[str, Any]) -> Dict[str, Any]:
            await asyncio.sleep(5)
            return {"fetched": 1}

        install_execute(monkeypatch, {"slow": slow, "fast": ok_handler})
        task_file = write_tasks(
            tmp_path,
            {
                "platforms": {
                    "slow": {"uids": ["1"], "timeout": 0.1},
                    "fast": {"uids": ["1", "2", "3"]},
                }
            },
        )

        results = await entry._run_all_tasks(task_file, "unused.yaml")

        by_platform = {r["platform"]: r for r in results}
        assert by_platform["slow"]["status"] == "timeout"
        assert "timeout" in by_platform["slow"]["error"]
        assert by_platform["fast"]["status"] == "ok"
        assert by_platform["fast"]["fetched"] == 3

    async def test_timeout_zero_means_unlimited(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        async def slow(platform: str, settings: Dict[str, Any]) -> Dict[str, Any]:
            await asyncio.sleep(0.15)
            return {"fetched": 1}

        install_execute(monkeypatch, {"slow": slow})
        task_file = write_tasks(
            tmp_path, {"platforms": {"slow": {"uids": ["1"], "timeout": 0}}}
        )

        results = await entry._run_all_tasks(task_file, "unused.yaml")

        assert results[0]["status"] == "ok"

    async def test_cancel_propagates(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        async def cancel_me(platform: str, settings: Dict[str, Any]) -> Dict[str, Any]:
            raise asyncio.CancelledError

        install_execute(monkeypatch, {"a": cancel_me})
        task_file = write_tasks(tmp_path, {"platforms": {"a": {"uids": ["1"]}}})

        with pytest.raises(asyncio.CancelledError):
            await entry._run_all_tasks(task_file, "unused.yaml")

    async def test_external_cancel_drains_pending(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """整批被外部取消时，其余任务要被收回，不留悬挂任务。"""
        started: List[str] = []

        async def slow(platform: str, settings: Dict[str, Any]) -> Dict[str, Any]:
            started.append(platform)
            await asyncio.sleep(30)
            return {"fetched": 0}

        install_execute(monkeypatch, {"a": slow, "b": slow})
        task_file = write_tasks(
            tmp_path, {"platforms": {"a": {"uids": ["1"]}, "b": {"uids": ["1"]}}}
        )

        batch = asyncio.ensure_future(entry._run_all_tasks(task_file, "unused.yaml"))
        await asyncio.sleep(0.05)
        batch.cancel()

        with pytest.raises(asyncio.CancelledError):
            await batch


class TestTaskSelection:
    """任务筛选：停用与非法配置不参与执行。"""

    async def test_disabled_task_is_skipped(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        calls = install_execute(monkeypatch, {"a": ok_handler, "b": ok_handler})
        task_file = write_tasks(
            tmp_path,
            {
                "platforms": {
                    "a": {"uids": ["1"]},
                    "b": {"uids": ["1"], "enabled": False},
                }
            },
        )

        results = await entry._run_all_tasks(task_file, "unused.yaml")

        assert calls == ["a"]
        assert [r["platform"] for r in results] == ["a"]

    async def test_malformed_settings_are_skipped(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        calls = install_execute(monkeypatch, {"a": ok_handler})
        task_file = write_tasks(
            tmp_path, {"platforms": {"a": {"uids": ["1"]}, "broken": "not-a-mapping"}}
        )

        results = await entry._run_all_tasks(task_file, "unused.yaml")

        assert calls == ["a"]
        assert len(results) == 1

    async def test_no_enabled_task_raises(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        install_execute(monkeypatch, {"a": ok_handler})
        task_file = write_tasks(
            tmp_path, {"platforms": {"a": {"uids": ["1"], "enabled": False}}}
        )

        with pytest.raises(ValueError, match="没有任何启用的平台任务"):
            await entry._run_all_tasks(task_file, "unused.yaml")

    async def test_empty_platforms_raises(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        task_file = write_tasks(tmp_path, {"platforms": {}})

        with pytest.raises(ValueError, match="未找到平台配置"):
            await entry._run_all_tasks(task_file, "unused.yaml")

    async def test_result_order_follows_config(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """结果顺序必须与配置声明顺序一致，而不是完成顺序。"""
        install_execute(
            monkeypatch,
            {
                "first": ok_handler,
                "second": ok_handler,
                "third": ok_handler,
            },
        )
        task_file = write_tasks(
            tmp_path,
            {
                "platforms": {
                    "first": {"uids": ["1"]},
                    "second": {"uids": ["1"]},
                    "third": {"uids": ["1"]},
                }
            },
        )

        results = await entry._run_all_tasks(task_file, "unused.yaml")

        assert [r["platform"] for r in results] == ["first", "second", "third"]


class TestConcurrency:
    """并发行为：默认全并发，可用 max_concurrent 收窄。"""

    async def test_tasks_run_concurrently(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        async def sleepy(platform: str, settings: Dict[str, Any]) -> Dict[str, Any]:
            await asyncio.sleep(0.2)
            return {"fetched": 1}

        install_execute(monkeypatch, {"a": sleepy, "b": sleepy, "c": sleepy})
        task_file = write_tasks(
            tmp_path,
            {
                "platforms": {
                    "a": {"uids": ["1"]},
                    "b": {"uids": ["1"]},
                    "c": {"uids": ["1"]},
                }
            },
        )

        started = time.perf_counter()
        results = await entry._run_all_tasks(task_file, "unused.yaml")
        elapsed = time.perf_counter() - started

        assert all(r["status"] == "ok" for r in results)
        # 并发执行：3 个 0.2s 的任务总耗时应显著小于 0.6s
        assert elapsed < 0.45

    async def test_max_concurrent_limits_parallelism(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        active = 0
        peak = 0

        async def tracked(platform: str, settings: Dict[str, Any]) -> Dict[str, Any]:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.05)
            active -= 1
            return {"fetched": 1}

        install_execute(monkeypatch, {"a": tracked, "b": tracked, "c": tracked})
        task_file = write_tasks(
            tmp_path,
            {
                "max_concurrent": 1,
                "platforms": {
                    "a": {"uids": ["1"]},
                    "b": {"uids": ["1"]},
                    "c": {"uids": ["1"]},
                },
            },
        )

        results = await entry._run_all_tasks(task_file, "unused.yaml")

        assert len(results) == 3
        assert peak == 1


class TestOverrides:
    """任务级配置覆盖：只把非调度键并入平台配置。"""

    def test_task_only_keys_are_filtered(self) -> None:
        settings = {
            "uids": ["1"],
            "full": True,
            "crawl_related": True,
            "max_related_per_user": 20,
            "max_depth": 2,
            "storage": "sqlite",
            "cookie_file": "x.json",
            "timeout": 60,
            "enabled": False,
            "snapshot": {"max_level": 3},
            "subtree_max_level": 2,
        }

        overrides = entry._build_overrides(settings)

        assert overrides == {
            "snapshot": {"max_level": 3},
            "subtree_max_level": 2,
        }

    async def test_settings_are_passed_through(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        seen: Dict[str, Any] = {}

        async def capture(platform: str, settings: Dict[str, Any]) -> Dict[str, Any]:
            seen.update(settings)
            return {"fetched": 0}

        install_execute(monkeypatch, {"a": capture})
        task_file = write_tasks(
            tmp_path,
            {"platforms": {"a": {"uids": ["1", "2"], "crawl_related": True}}},
        )

        await entry._run_all_tasks(task_file, "unused.yaml")

        assert seen["uids"] == ["1", "2"]
        assert seen["crawl_related"] is True


class TestSummary:
    """摘要统计。"""

    def test_summarize_counts(self) -> None:
        results = [
            {"platform": "a", "status": "ok", "fetched": 5},
            {"platform": "b", "status": "error", "fetched": 0},
            {"platform": "c", "status": "timeout", "fetched": 0},
        ]

        summary = entry._summarize(results, cycle=3)

        assert summary["total_tasks"] == 3
        assert summary["succeeded"] == 1
        assert summary["failed"] == 2
        assert summary["total_fetched"] == 5
        assert summary["cycle"] == 3

    def test_summarize_without_cycle(self) -> None:
        summary = entry._summarize([{"platform": "a", "status": "ok", "fetched": 1}])

        assert "cycle" not in summary
        assert summary["succeeded"] == 1


class TestMainExitCodes:
    """主入口退出码：0 全成功 / 1 有失败 / 2 配置错误。"""

    def _point_at(self, monkeypatch: pytest.MonkeyPatch, task_file: str) -> None:
        monkeypatch.setattr(entry, "TASK_FILE", task_file)

    async def test_all_success_returns_zero(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        install_execute(monkeypatch, {"a": ok_handler})
        self._point_at(
            monkeypatch,
            write_tasks(tmp_path, {"platforms": {"a": {"uids": ["1"]}}}),
        )

        assert await entry.main() == 0

    async def test_any_failure_returns_one(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        install_execute(monkeypatch, {"a": ok_handler, "b": boom_handler})
        self._point_at(
            monkeypatch,
            write_tasks(
                tmp_path,
                {"platforms": {"a": {"uids": ["1"]}, "b": {"uids": ["1"]}}},
            ),
        )

        assert await entry.main() == 1

    async def test_bad_config_returns_two(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        self._point_at(monkeypatch, write_tasks(tmp_path, {"platforms": {}}))

        assert await entry.main() == 2

    async def test_missing_task_file_returns_two(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        self._point_at(monkeypatch, str(tmp_path / "nope.yaml"))

        assert await entry.main() == 2
