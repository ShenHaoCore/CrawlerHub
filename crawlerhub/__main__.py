"""任务调度入口（**无命令行参数**，全部由 ``configs/tasks.yaml`` 驱动）。

用法::

    python -m crawlerhub

入口读取 ``configs/tasks.yaml``，把 ``platforms`` 下的每个平台当作一个
**独立任务**，在同一个事件循环里并发启动。任务之间彼此不干扰：

=================  ==========================================================
隔离维度            说明
=================  ==========================================================
配置               每个任务各自加载一份配置（base + 平台 + 任务级覆盖），
                   不共享任何可变状态
资源               HTTP 客户端 / Cookie / 去重器 / 存储实例按任务独立创建
异常               任务抛出的任何异常都在任务边界收敛为 ``status: error``，
                   不会被抛出而中断其余任务
超时               单任务超时（``timeout`` 秒）只终止该任务自己
取消               Ctrl+C 等取消信号会正常向上传播，便于整体退出
=================  ==========================================================

``configs/tasks.yaml`` 可用配置：

- ``schedule.enabled`` / ``schedule.interval``：定时循环执行整批任务；
- ``max_concurrent``：同时运行的任务数上限，``0`` 或省略表示不限；
- 平台下的 ``enabled``：置 ``false`` 可临时停用该任务，无需删除配置；
- 平台下的 ``timeout``：该任务的超时秒数，``0`` 或省略表示不限；
- 平台下的其余键：作为覆盖项合并进平台配置，因此 ``tasks.yaml`` 也可以
  覆盖 ``configs/platforms/<平台>.yaml`` 里的参数。
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any, Coroutine, Dict, List, Optional

from crawlerhub.core.config import Config, load_config, load_yaml
from crawlerhub.core.cookie import CookieManager
from crawlerhub.core.exceptions import CrawlerHubError
from crawlerhub.core.logger import logger, setup_logger
from crawlerhub.platforms.registry import get_platform_cls

CONFIG_DIR = Path(__file__).resolve().parent.parent / "configs"
COOKIE_DIR = Path(__file__).resolve().parent.parent / "data" / "cookies"
TASK_FILE = CONFIG_DIR / "tasks.yaml"
BASE_CONFIG = CONFIG_DIR / "base.yaml"

#: tasks.yaml 中由调度器消费、不参与「平台配置覆盖」的键
TASK_ONLY_KEYS = frozenset(
    {
        "uids",
        "full",
        "crawl_related",
        "max_related_per_user",
        "max_depth",
        "storage",
        "cookie_file",
        "timeout",
        "enabled",
    }
)


def _init_logging(base_config: Config) -> None:
    """初始化日志。"""
    setup_logger(
        level=base_config.get("logging.level", "INFO"),
        log_file=base_config.get("logging.file"),
        rotation=base_config.get("logging.rotation", "10 MB"),
        retention=base_config.get("logging.retention", "7 days"),
    )


def _build_overrides(settings: Dict[str, Any]) -> Dict[str, Any]:
    """把任务配置里非调度键的部分转成对平台配置的覆盖项。

    Args:
        settings: ``tasks.yaml`` 中某个平台下的配置字典。

    Returns:
        可传给 :meth:`Config.override` 的覆盖字典。
    """
    return {key: value for key, value in settings.items() if key not in TASK_ONLY_KEYS}


async def _execute_platform(
    platform: str,
    settings: Dict[str, Any],
    base_config_path: str,
) -> Dict[str, Any]:
    """单个平台任务的执行体（不含隔离包装）。

    只负责把配置、Cookie、爬虫实例组装起来并跑完，异常照常向上抛，
    由 :func:`_run_platform_task` 在任务边界统一收敛。

    Args:
        platform: 平台名称。
        settings: 该平台在 ``tasks.yaml`` 中的配置。
        base_config_path: 全局配置文件路径。

    Returns:
        形如 ``{"fetched": n, "requests": n, ...}`` 的执行结果。

    Raises:
        ValueError: 配置不完整，或平台不支持所请求的模式。
    """
    uids = [str(uid) for uid in (settings.get("uids") or [])]
    full = bool(settings.get("full", False))
    if not uids and not full:
        raise ValueError("未配置 uids，且未开启 full 全量模式")

    platform_cfg_path = CONFIG_DIR / "platforms" / f"{platform}.yaml"
    config = load_config(base_config_path, platform_cfg_path)

    overrides = _build_overrides(settings)
    if overrides:
        config.override(overrides)

    # storage 既接受后端名简写（"sqlite"），也接受完整覆盖（{"output_dir": ...}）
    storage_override = settings.get("storage")
    if isinstance(storage_override, str):
        config.override({"storage": {"default": storage_override}})
    elif isinstance(storage_override, dict):
        config.override({"storage": storage_override})

    cookie_file = settings.get("cookie_file") or str(COOKIE_DIR / f"{platform}.json")
    cookie_mgr = CookieManager(
        cookie_file=cookie_file,
        auto_refresh=config.get("cookie.auto_refresh", False),
        refresh_interval=config.get("cookie.refresh_interval", 1800),
    )

    crawler_cls = get_platform_cls(platform)
    crawler = crawler_cls(config=config, cookie_manager=cookie_mgr)

    # 全量快照模式：由平台自行实现 run_all（如 xzqh）
    if full:
        run_all = getattr(crawler, "run_all", None)
        if not callable(run_all):
            raise ValueError(f"平台 {platform} 未实现 run_all，不支持 full 全量模式")
        snapshot = await run_all()
        records = snapshot.get("records") or []
        return {
            "fetched": snapshot.get("node_count", len(records)),
            "requests": snapshot.get("requests", 0),
            "written": snapshot.get("written", {}),
        }

    results = await crawler.run(
        uids,
        crawl_related=bool(settings.get("crawl_related", False)),
        max_related=int(settings.get("max_related_per_user", 50)),
        max_depth=int(settings.get("max_depth", 1)),
    )
    return {"fetched": len(results), "requests": 0}


async def _run_platform_task(
    platform: str,
    settings: Dict[str, Any],
    base_config_path: str,
    semaphore: Optional[asyncio.Semaphore] = None,
) -> Dict[str, Any]:
    """执行单个平台任务，并把一切异常与超时收敛为结果摘要。

    这是任务隔离的关键边界：除取消信号外，本函数**不会向上抛异常**，
    因此任何一个任务失败或卡死都不会影响其他任务。

    Args:
        platform: 平台名称。
        settings: 该平台在 ``tasks.yaml`` 中的配置。
        base_config_path: 全局配置文件路径。
        semaphore: 可选的并发闸门，``None`` 表示不限制并发。

    Returns:
        统一格式的任务结果，``status`` 为 ``ok`` / ``error`` / ``timeout``。

    Raises:
        asyncio.CancelledError: 收到取消信号时原样向上传播。
    """

    async def _guarded() -> Dict[str, Any]:
        if semaphore is None:
            return await _execute_platform(platform, settings, base_config_path)
        async with semaphore:
            return await _execute_platform(platform, settings, base_config_path)

    timeout = float(settings.get("timeout") or 0.0)
    started = time.perf_counter()

    try:
        if timeout > 0:
            payload = await asyncio.wait_for(_guarded(), timeout=timeout)
        else:
            payload = await _guarded()
    except asyncio.CancelledError:
        logger.warning(f"[{platform}] 任务收到取消信号，正在退出")
        raise
    except asyncio.TimeoutError:
        elapsed = time.perf_counter() - started
        logger.error(
            f"[{platform}] 任务超时（限 {timeout:g}s，实跑 {elapsed:.1f}s），"
            "已单独终止，不影响其他任务"
        )
        return {
            "platform": platform,
            "status": "timeout",
            "fetched": 0,
            "elapsed": round(elapsed, 1),
            "error": f"timeout after {timeout:g}s",
        }
    except Exception as exc:  # noqa: BLE001 - 任务边界，需兜住所有异常
        elapsed = time.perf_counter() - started
        logger.error(
            f"[{platform}] 任务失败，不影响其他任务: {type(exc).__name__}: {exc}"
        )
        logger.debug(f"[{platform}] 失败堆栈", exc_info=True)
        return {
            "platform": platform,
            "status": "error",
            "fetched": 0,
            "elapsed": round(elapsed, 1),
            "error": f"{type(exc).__name__}: {exc}",
        }

    elapsed = time.perf_counter() - started
    logger.info(
        f"[{platform}] 任务完成，抓取 {payload.get('fetched', 0)} 条，"
        f"耗时 {elapsed:.1f}s"
    )
    return {
        "platform": platform,
        "status": "ok",
        "elapsed": round(elapsed, 1),
        **payload,
    }


async def _run_all_tasks(
    task_file: str,
    base_config_path: str,
) -> List[Dict[str, Any]]:
    """并发执行任务文件里所有启用的平台任务。

    Args:
        task_file: 任务配置文件路径。
        base_config_path: 全局配置文件路径。

    Returns:
        每个任务一条结果摘要，顺序与配置文件中的声明顺序一致。

    Raises:
        ValueError: 任务文件不存在有效任务。
    """
    tasks_cfg = load_yaml(task_file)
    platforms = tasks_cfg.get("platforms") or {}
    if not platforms:
        raise ValueError(f"任务文件中未找到平台配置: {task_file}")

    limit = int(tasks_cfg.get("max_concurrent") or 0)
    semaphore = asyncio.Semaphore(limit) if limit > 0 else None

    # 先筛出真正要执行的任务，保证 names 与任务列表严格一一对应
    names: List[str] = []
    coros: List[Coroutine[Any, Any, Dict[str, Any]]] = []
    for platform, settings in platforms.items():
        if not isinstance(settings, dict):
            logger.warning(f"[{platform}] 配置格式异常（应为映射），跳过")
            continue
        if not settings.get("enabled", True):
            logger.info(f"[{platform}] 已停用（enabled: false），跳过")
            continue
        names.append(platform)
        coros.append(
            _run_platform_task(platform, settings, base_config_path, semaphore)
        )

    if not coros:
        raise ValueError("任务文件中没有任何启用的平台任务")

    scope = f"并发上限 {limit}" if limit > 0 else "不限并发"
    logger.info(f"启动 {len(coros)} 个任务（{scope}）: {', '.join(names)}")

    jobs = [asyncio.ensure_future(coro) for coro in coros]

    async def _drain_pending() -> None:
        """收回仍在运行的任务，避免事件循环关闭时留下悬挂任务。"""
        for job in jobs:
            if not job.done():
                job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)

    # return_exceptions=True：单个任务异常不会取消其他任务。
    # 取消信号（CancelledError 属于 BaseException）要单独识别并继续传播。
    try:
        gathered = await asyncio.gather(*jobs, return_exceptions=True)
    except asyncio.CancelledError:
        await _drain_pending()
        raise

    results: List[Dict[str, Any]] = []
    for platform, item in zip(names, gathered):
        if isinstance(item, asyncio.CancelledError):
            await _drain_pending()
            raise item
        if isinstance(item, BaseException):
            logger.error(f"[{platform}] 任务抛出未预期异常: {item!r}")
            results.append(
                {
                    "platform": platform,
                    "status": "error",
                    "fetched": 0,
                    "error": f"{type(item).__name__}: {item}",
                }
            )
        else:
            results.append(item)
    return results


def _summarize(results: List[Dict[str, Any]], cycle: Optional[int] = None) -> Dict[str, Any]:
    """把任务结果列表汇总成一份摘要。

    Args:
        results: :func:`_run_all_tasks` 的返回值。
        cycle: 定时模式下的轮次编号。

    Returns:
        汇总摘要字典。
    """
    failed = [r for r in results if r.get("status") != "ok"]
    summary: Dict[str, Any] = {
        "mode": "multi-platform",
        "total_tasks": len(results),
        "succeeded": len(results) - len(failed),
        "failed": len(failed),
        "total_fetched": sum(int(r.get("fetched", 0)) for r in results),
        "tasks": results,
    }
    if cycle is not None:
        summary["cycle"] = cycle
    return summary


async def main() -> int:
    """主入口：读取 ``configs/tasks.yaml`` 并并发执行所有平台任务。

    ``schedule.enabled`` 为 true 时按 ``schedule.interval`` 秒循环执行整批
    任务；每轮都会重新读取配置文件，便于不重启即调整任务。

    Returns:
        退出码：``0`` 全部成功，``1`` 有任务失败，``2`` 任务文件配置有问题；
        定时模式始终返回 ``0``。
    """
    base_config = load_yaml(BASE_CONFIG)
    _init_logging(Config(base_config, {}))
    logger.info(f"任务文件: {TASK_FILE}")

    try:
        tasks_cfg = load_yaml(str(TASK_FILE))
    except CrawlerHubError as exc:
        logger.error(f"任务文件不可用: {exc}")
        return 2

    schedule = tasks_cfg.get("schedule") or {}
    enabled = bool(schedule.get("enabled", False))
    interval = float(schedule.get("interval", 3600))

    if enabled:
        logger.info(f"定时持续运行已启用，间隔 {interval:.0f} 秒")
        cycle = 0
        while True:
            cycle += 1
            logger.info(f"========== 第 {cycle} 轮开始 ==========")
            try:
                results = await _run_all_tasks(str(TASK_FILE), str(BASE_CONFIG))
                summary = _summarize(results, cycle=cycle)
                logger.info(
                    f"第 {cycle} 轮结束：成功 {summary['succeeded']}/{summary['total_tasks']}，"
                    f"共抓取 {summary['total_fetched']} 条"
                )
                print(json.dumps(summary, ensure_ascii=False, indent=2))
            except Exception as exc:  # noqa: BLE001 - 整轮兜底，避免循环中断
                logger.error(f"第 {cycle} 轮异常: {exc}")
            logger.info(f"等待 {interval:.0f} 秒后进入下一轮（Ctrl+C 停止）")
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                logger.info("收到停止信号，退出")
                break
        return 0

    try:
        results = await _run_all_tasks(str(TASK_FILE), str(BASE_CONFIG))
    except (ValueError, CrawlerHubError) as exc:
        logger.error(f"任务配置有误，无法启动: {exc}")
        return 2

    summary = _summarize(results)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if summary["failed"] else 0


def run() -> None:
    """同步入口。"""
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\n用户中断")
        sys.exit(130)


if __name__ == "__main__":
    run()
