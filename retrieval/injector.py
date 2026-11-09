# -*- coding: utf-8 -*-
"""裁剪注入：把 rerank 后的用例列表拼成 prompt 片段。"""
from __future__ import annotations

from config import RETR_STEP_CUTOFF


def _truncate(text: str, n: int) -> str:
    if not text:
        return ""
    t = text.replace("\n", " ").strip()
    return t if len(t) <= n else t[:n] + "..."


def inject(cases: list[dict], step_cutoff: int | None = None) -> str:
    """
    把 TopK 用例拼成 prompt 片段。
    每条只保留 case_name / priority / expected 全文 / steps 前 N 字符。
    """
    n = step_cutoff if step_cutoff is not None else RETR_STEP_CUTOFF
    if not cases:
        return "(未检索到相关用例)"

    lines = []
    lines.append(f"## 相关测试用例（共 {len(cases)} 条）")
    lines.append("")
    for i, c in enumerate(cases, 1):
        name = c.get("case_name", "(未知)")
        pri = c.get("priority", "-")
        cat = c.get("catalog_path", "")
        exp = (c.get("expected") or "").strip()
        stp = _truncate(c.get("steps", ""), n)
        result = c.get("last_result") or c.get("execute_result", "")
        lines.append(f"{i}. **{name}** [{pri}]{f' 结果: {result}' if result else ''}")
        if cat:
            lines.append(f"   分类: {cat}")
        if stp:
            lines.append(f"   步骤: {stp}")
        if exp:
            lines.append(f"   预期: {exp}")
        lines.append("")
    return "\n".join(lines)


def count_chars(text: str) -> int:
    """粗略估算 token 数：中文 1 字 ≈ 2 token，英文 1 词 ≈ 1 token。
    为了简单，直接用字符数除以 2.5。"""
    return len(text) // 3
