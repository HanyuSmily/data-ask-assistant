# -*- coding: utf-8 -*-
"""用例库注入模块：
- 启动时加载清洗后的 cases_clean.json
- 提供 get_case_library_prompt() 返回按优先级排序 + token 预算截断的 system prompt 片段
- chat_handler 调用时把这个片段插到 prompt 文件里的 {CASE_LIBRARY} 占位符
"""
import json
import os

# 预算常量（可按需调整）
TOKEN_BUDGET_CASE_LIBRARY = 8000   # 用例库片段最多分配这么多 token
ZH_CHAR = set("，。；：？！、（）【】《》""''——…·\u4e00-\u9fff")

_cases_cache: list[dict] | None = None


def estimate_tokens(text: str) -> int:
    zh = sum(1 for c in text if c in ZH_CHAR)
    other = sum(1 for c in text if c not in ZH_CHAR)
    return int(zh * 1.5 + other * 0.25)


def load_cases() -> list[dict]:
    """懒加载清洗后的用例，全局单例"""
    global _cases_cache
    if _cases_cache is None:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cases_clean.json")
        with open(p, encoding="utf-8") as f:
            raw = json.load(f)
        # 按优先级排序（P0 先，P1 中，P2 后），同优先级保留原序
        prio_order = {"P0": 0, "P1": 1, "P2": 2, "": 3}
        _cases_cache = sorted(raw, key=lambda c: prio_order.get(c.get("priority", ""), 3))
    return _cases_cache


def _format_case(c: dict) -> str:
    parts = [f"[{c['case_id']}] {c['case_name']}"]
    if c["priority"]:
        parts.append(f"  优先级: {c['priority']}")
    if c["catalog_tag"]:
        parts.append(f"  分类: {c['catalog_tag']}")
    if c["precondition"]:
        parts.append(f"  前置: {c['precondition']}")
    if c["step"]:
        parts.append(f"  步骤: {c['step']}")
    if c["expect"]:
        parts.append(f"  预期: {c['expect']}")
    return "\n".join(parts)


def get_case_library_prompt(max_tokens: int | None = None) -> str:
    """返回按优先级排序、受 token 预算截断的用例库 prompt 段
    Args:
        max_tokens: 覆盖全局预算；默认用 TOKEN_BUDGET_CASE_LIBRARY
    Returns:
        可直接插入 system prompt 的文本；若用例库为空返回空串
    """
    budget = max_tokens or TOKEN_BUDGET_CASE_LIBRARY
    cases = load_cases()
    if not cases:
        return ""

    used = 0
    included = []
    for c in cases:
        text = _format_case(c)
        t = estimate_tokens(text) + 1
        if used + t > budget:
            break
        used += t
        included.append(text)

    header = f"# 参考用例库（共 {len(cases)} 条，已注入 {len(included)} 条，按优先级 P0>P1>P2 排序）\n\n"
    return header + "\n\n".join(included)


def main():
    """命令行预览：python cases/integrate_cases.py"""
    prompt = get_case_library_prompt()
    cases = load_cases()
    print(f"用例总数: {len(cases)}")
    print(f"注入片段 token 估算: ≈ {estimate_tokens(prompt)}")
    print(f"注入片段字符长度: {len(prompt)}")
    print(f"\n--- 前 500 字预览 ---")
    print(prompt[:500])


if __name__ == "__main__":
    main()
