"""国家地名信息库行政区划数据解析器。

将 ``/9095/xzqh/getList`` 接口返回的嵌套区划树，解析为统一结构的扁平记录。

接口响应结构::

    {
      "data": {
        "code": "420000000000",
        "name": "湖北省",
        "level": 1,
        "type": "省",
        "children": [ ... 递归 ... ]
      },
      "message": null,
      "status": 200,
      "total": 1,
      "tag": null
    }

字段含义：

- ``code``：12 位行政区划代码。省/地/县三级的前 6 位即 GB/T 2260 代码；
  乡级的前 6 位指向其所属县级单位（12 位 = 6 位区划码 + 6 位扩展码）。
  少数单位的 ``code`` 不是数字——例如台湾省为占位文本 ``资料暂缺``，
  此时 ``code6`` 会取空串而非截断文本。
- ``level``：``1`` 省级、``2`` 地级、``3`` 县级、``4`` 乡级。
- ``type``：区划单位类型，如 ``省``/``自治区``/``直辖市``/``地级市``/
  ``市辖区``/``县``/``街道``/``镇``。

解析规则集中在此模块，便于接口改版时集中维护。
"""

from __future__ import annotations

from typing import Any, Dict, Iterator, List, Sequence, Tuple

from crawlerhub.core.exceptions import ParseError

#: ``level`` 数值到中文口径的映射
LEVEL_NAMES: Dict[int, str] = {1: "省级", 2: "地级", 3: "县级", 4: "乡级"}

#: 按 ``level`` 归类的祖先前缀字段，用于给每条记录补上所属省/地/县
ANCESTOR_FIELDS: Dict[int, Tuple[str, str]] = {
    1: ("province_code", "province_name"),
    2: ("prefecture_code", "prefecture_name"),
    3: ("county_code", "county_name"),
}

#: 接口成功时的业务状态码
OK_STATUS = 200


def numeric_code6(code: str) -> str:
    """取行政区划代码的前 6 位。

    上游对少数单位返回的不是数字代码——例如台湾省的 ``code`` 为占位文本
    ``资料暂缺``。此时返回空串，保证 ``code6`` 要么是合法的 6 位数字码，
    要么为空，便于下游安全地按 6 位码处理。

    Args:
        code: 12 位行政区划代码，或上游的占位文本。

    Returns:
        6 位数字码，无法判定时返回空串。
    """
    head = code[:6]
    return head if len(head) == 6 and head.isdigit() else ""


def parse_list_payload(payload: Any, *, code: str = "") -> Dict[str, Any]:
    """校验并解包 ``getList`` 接口响应，返回根节点。

    Args:
        payload: 接口返回的 JSON 对象。
        code: 本次请求的区划代码，仅用于错误信息。

    Returns:
        根节点字典，含 ``code``/``name``/``level``/``type``/``children``。

    Raises:
        ParseError: 响应不是对象、业务状态码非 200，或 ``data`` 字段缺失。
    """
    if not isinstance(payload, dict):
        raise ParseError(
            f"区划接口返回非对象: {type(payload).__name__}",
            raw=str(payload)[:200],
        )

    status = payload.get("status")
    if status != OK_STATUS:
        raise ParseError(
            f"区划接口业务状态异常: status={status} "
            f"message={payload.get('message')} code={code or '(全国)'}",
            raw=str(payload)[:200],
        )

    data = payload.get("data")
    if not isinstance(data, dict):
        raise ParseError(
            f"区划接口 data 字段缺失或类型异常: code={code or '(全国)'}",
            raw=str(payload)[:200],
        )
    return data


def iter_nodes(
    node: Dict[str, Any],
    ancestors: Tuple[Dict[str, Any], ...] = (),
) -> Iterator[Tuple[Dict[str, Any], Tuple[Dict[str, Any], ...]]]:
    """深度优先遍历区划树，逐个产出 ``(节点, 祖先元组)``。

    祖先元组自顶向下排列，且**不包含当前节点自身**。这样调用方既能拿到
    节点本身，也能据此还原完整路径。

    Args:
        node: 当前节点。
        ancestors: 当前节点的祖先节点元组。

    Yields:
        ``(节点, 祖先元组)`` 二元组。
    """
    yield node, ancestors
    for child in node.get("children") or []:
        if isinstance(child, dict):
            yield from iter_nodes(child, ancestors + (node,))


def nodes_at_depth(
    root: Dict[str, Any],
    depth: int,
) -> List[Tuple[Dict[str, Any], Tuple[Dict[str, Any], ...]]]:
    """返回相对 ``root`` 深度为 ``depth`` 的节点。

    ``root`` 自身深度为 ``0``。返回的节点是原树中的**同一对象**，
    因此调用方可以直接就地修改它（例如把下钻拿到的乡级挂回树上）。

    Args:
        root: 子树根节点。
        depth: 相对深度。

    Returns:
        ``(节点, 祖先元组)`` 列表，按深度优先顺序排列。
    """
    return [(node, anc) for node, anc in iter_nodes(root) if len(anc) == depth]


def flatten_node(
    node: Dict[str, Any],
    ancestors: Sequence[Dict[str, Any]] = (),
) -> Dict[str, Any]:
    """把单个区划节点摊平成一条记录。

    Args:
        node: 区划节点。
        ancestors: 自顶向下的祖先节点序列。

    Returns:
        扁平记录字典，除接口原有字段外还补充：

        - ``full_name``：自省到本级的完整名称，如 ``湖北省武汉市江岸区大智街道``。
        - ``parent_code`` / ``parent_name``：直接上级（全国根节点会被忽略）。
        - ``province_*`` / ``prefecture_*`` / ``county_*``：按 ``level`` 归类的归属。
    """
    code = str(node.get("code") or "")
    raw_level = node.get("level")
    level = int(raw_level) if isinstance(raw_level, int) else None

    # 过滤虚拟根（全国根节点 name 为空、code 为 "00"），它不进 full_name
    visible = [anc for anc in ancestors if anc.get("name")]
    chain: List[Dict[str, Any]] = visible + [node]

    parent = visible[-1] if visible else {}
    full_name = "".join(str(item.get("name") or "") for item in chain)

    record: Dict[str, Any] = {
        "platform": "xzqh",
        "code": code,
        "code6": numeric_code6(code),
        "name": str(node.get("name") or ""),
        "level": level,
        "level_name": LEVEL_NAMES.get(level or 0, ""),
        "type": str(node.get("type") or ""),
        "parent_code": str(parent.get("code") or ""),
        "parent_name": str(parent.get("name") or ""),
        "full_name": full_name,
    }

    for level_value, (code_key, name_key) in ANCESTOR_FIELDS.items():
        matched = next(
            (item for item in reversed(chain) if item.get("level") == level_value),
            None,
        )
        record[code_key] = str(matched.get("code") or "") if matched else ""
        record[name_key] = str(matched.get("name") or "") if matched else ""

    return record


def flatten_tree(
    root: Dict[str, Any],
    ancestors: Sequence[Dict[str, Any]] = (),
    *,
    dedup: bool = True,
) -> List[Dict[str, Any]]:
    """遍历整棵区划树，返回扁平记录列表。

    全国虚拟根（``code`` 为 ``"00"``、``name`` 为空）会被跳过，不产生记录。

    Args:
        root: 子树根节点。
        ancestors: 根节点的祖先序列，用于补全 ``full_name`` 等归属字段。
        dedup: 是否按 ``code`` 去重（防止上游数据出现重复分支）。

    Returns:
        扁平记录列表，按深度优先顺序排列。
    """
    records: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for node, node_ancestors in iter_nodes(root, tuple(ancestors)):
        # 跳过全国虚拟根：它 code 为 "00"、name 为空，不是实际行政区划
        if not node.get("name"):
            continue
        record = flatten_node(node, node_ancestors)
        if dedup:
            if record["code"] in seen:
                continue
            seen.add(record["code"])
        records.append(record)

    return records


def count_nodes(root: Dict[str, Any]) -> int:
    """统计子树中的节点总数（含根节点）。"""
    return sum(1 for _ in iter_nodes(root))
