"""哈希工具：用于内容去重的稳定哈希生成。"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def sha256_hex(data: str | bytes) -> str:
    """对字符串或字节生成 SHA-256 十六进制摘要。

    Args:
        data: 待哈希的字符串或字节。

    Returns:
        64 位十六进制哈希字符串。
    """
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def content_hash(obj: Any) -> str:
    """对任意可序列化对象生成稳定哈希。

    用于内容去重：相同内容（忽略键顺序）生成相同哈希。

    Args:
        obj: 任意可被 json.dumps 序列化的对象。

    Returns:
        64 位十六进制哈希字符串。
    """
    serialized = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)
    return sha256_hex(serialized)


def url_hash(url: str) -> str:
    """对 URL 生成去重哈希。

    Args:
        url: 目标 URL。

    Returns:
        64 位十六进制哈希字符串。
    """
    return sha256_hex(url.strip())
