"""crawlerhub.utils.proxy 模块测试。"""

from crawlerhub.utils.proxy import ProxyPool
from crawlerhub.core.exceptions import ProxyError


class TestProxyPool:
    def test_load_proxies(self, tmp_path) -> None:
        """正常加载代理列表。"""
        proxy_file = tmp_path / "proxies.txt"
        proxy_file.write_text(
            "http://proxy1:8080\n"
            "# 这是注释\n"
            "http://proxy2:8080\n"
            "\n"
            "http://user:pass@proxy3:3128\n",
            encoding="utf-8",
        )
        pool = ProxyPool(proxy_file)
        assert len(pool) == 3
        assert "http://proxy1:8080" in pool.all()

    def test_random_returns_valid_proxy(self, tmp_path) -> None:
        proxy_file = tmp_path / "proxies.txt"
        proxy_file.write_text("http://proxy1:8080\nhttp://proxy2:8080\n", encoding="utf-8")
        pool = ProxyPool(proxy_file)
        result = pool.random()
        assert result in ["http://proxy1:8080", "http://proxy2:8080"]

    def test_empty_file_raises_error(self, tmp_path) -> None:
        proxy_file = tmp_path / "proxies.txt"
        proxy_file.write_text("# 只有注释\n\n", encoding="utf-8")
        try:
            pool = ProxyPool(proxy_file)
            assert False, "应抛出 ProxyError"
        except ProxyError:
            pass

    def test_nonexistent_file_raises_error(self, tmp_path) -> None:
        proxy_file = tmp_path / "nonexistent.txt"
        try:
            pool = ProxyPool(proxy_file)
            assert False, "应抛出 ProxyError"
        except ProxyError:
            pass

    def test_all_returns_copy(self, tmp_path) -> None:
        proxy_file = tmp_path / "proxies.txt"
        proxy_file.write_text("http://proxy1:8080\n", encoding="utf-8")
        pool = ProxyPool(proxy_file)
        all_proxies = pool.all()
        all_proxies.append("http://fake:9999")
        # 原始池不受影响
        assert len(pool) == 1
