"""crawlerhub.utils.hash 模块测试。"""

from crawlerhub.utils.hash import content_hash, sha256_hex, url_hash


class TestSha256Hex:
    def test_string_input(self) -> None:
        result = sha256_hex("hello")
        assert len(result) == 64
        assert result == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"

    def test_bytes_input(self) -> None:
        result = sha256_hex(b"hello")
        assert result == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"

    def test_string_and_bytes_equivalent(self) -> None:
        assert sha256_hex("hello") == sha256_hex(b"hello")

    def test_different_inputs_different_hashes(self) -> None:
        assert sha256_hex("a") != sha256_hex("b")

    def test_empty_string(self) -> None:
        result = sha256_hex("")
        assert len(result) == 64


class TestContentHash:
    def test_same_content_same_hash(self) -> None:
        """相同内容（键顺序不同）应生成相同哈希。"""
        assert content_hash({"a": 1, "b": 2}) == content_hash({"b": 2, "a": 1})

    def test_different_content_different_hash(self) -> None:
        assert content_hash({"a": 1}) != content_hash({"a": 2})

    def test_nested_dict(self) -> None:
        data1 = {"user": {"name": "test", "id": 1}, "count": 2}
        data2 = {"count": 2, "user": {"id": 1, "name": "test"}}
        assert content_hash(data1) == content_hash(data2)

    def test_list_order_matters(self) -> None:
        """列表顺序不同应生成不同哈希。"""
        assert content_hash([1, 2]) != content_hash([2, 1])

    def test_returns_hex_string(self) -> None:
        result = content_hash({"test": "data"})
        assert len(result) == 64
        int(result, 16)  # 验证是合法的十六进制


class TestUrlHash:
    def test_basic(self) -> None:
        result = url_hash("https://example.com")
        assert len(result) == 64

    def test_strips_whitespace(self) -> None:
        """前后空格应被去除，生成相同哈希。"""
        assert url_hash("  https://example.com  ") == url_hash("https://example.com")

    def test_different_urls_different_hashes(self) -> None:
        assert url_hash("https://a.com") != url_hash("https://b.com")
