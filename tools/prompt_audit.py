# -*- coding: utf-8 -*-
"""Prompt 审计工具：打印每轮实际发给 LLM 的 prompt 与 token 分解。

用法（项目根目录）：
    python tools/prompt_audit.py            # 全模式对比
    python tools/prompt_audit.py --show 陪练模式   # 打印该模式完整 prompt
    python tools/prompt_audit.py --json out.json  # 导出结构化结果

瘦身后口径：
    system_base = 骨架 + 命中详情 + 紧凑模块索引（不含 TopK）→ 硬性 ≤800 token
    topk / history / user 单独计量，不参与 800 上限
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)

import chat_handler as ch  # noqa: E402

BASELINE_DIR = os.path.join(_HERE, "_baseline_prompts")

# 场景：mode / 用户输入 / 说明
SCENARIOS = [
    ("陪练模式", "摄像头晚上画面全黑，白天正常", "常态轮（不命中详情）"),
    ("陪练模式", "教我六步定位法怎么用", "命中 six_step"),
    ("陪练模式", "设备配网一直失败", "命中 ipc_modules"),
    ("速查模式", "FTS5 的 bm25 排序是怎么算的", "常态轮"),
    ("出题模式", "帮我出一道测试题", "常态轮"),
    ("出题模式", "我答完了，帮我评分", "命中 quiz_rubric"),
    ("执行模式", "执行用例 1536949", "常态轮（走工具链，不调 LLM）"),
]


def _baseline_system(mode: str, user_text: str) -> tuple[int, int]:
    """复现瘦身前口径：详情版模块索引 + 旧骨架，不含 TopK。"""
    from retrieval.db import format_module_index_for_prompt
    fname = ch.MODE_PROMPT[mode]
    path = os.path.join(BASELINE_DIR, fname)
    if not os.path.exists(path):
        return 0, 0
    with open(path, encoding="utf-8") as f:
        skeleton = f.read()
    try:
        idx = format_module_index_for_prompt(ch._get_retrieval_conn())
    except Exception:
        idx = ""
    skeleton = skeleton.replace("{CASE_LIBRARY}", idx)
    t_sk = ch.estimate_tokens(skeleton) - ch.estimate_tokens(idx)
    return t_sk, ch.estimate_tokens(skeleton)


def run(show: str | None = None) -> list[dict]:
    rows: list[dict] = []
    for mode, query, note in SCENARIOS:
        info = ch.build_prompt(mode, query, history=None)
        bd = info["breakdown"]
        base_sk, base_total = _baseline_system(mode, query)
        rows.append({
            "mode": mode,
            "query": query,
            "note": note,
            "details": info["details"],
            "skeleton": bd["skeleton"],
            "detail_tok": bd["details"],
            "index": bd["module_index"],
            "system_base": bd["system_base"],
            "topk": bd["topk"],
            "history": bd["history"],
            "user": bd["user"],
            "total_input": bd["total_input"],
            "over_budget": info["over_budget"],
            "base_skeleton": base_sk,
            "base_system": base_total,
            "saved": base_total - bd["system_base"],
            "saved_pct": round((1 - bd["system_base"] / base_total) * 100, 1) if base_total else 0,
        })
        if show and mode == show:
            _print_full(info)
    return rows


def _print_full(info: dict) -> None:
    print("\n" + "=" * 70)
    print(f"【{info['mode']}】实际发送的 messages（{len(info['messages'])} 条）")
    print("=" * 70)
    for m in info["messages"]:
        print(f"\n----- role={m['role']} -----")
        print(m["content"])
    print("\n" + "=" * 70)
    bd = info["breakdown"]
    print(f"骨架={bd['skeleton']} 详情={bd['details']} 索引={bd['module_index']} "
          f"system(不含TopK)={bd['system_base']}/800  TopK={bd['topk']}  总输入={bd['total_input']}")
    print("=" * 70 + "\n")


def print_table(rows: list[dict]) -> None:
    print("\n### 瘦身后：每轮实际 prompt 的 token 分解\n")
    hdr = f"{'模式':<6}{'系统prompt(不含TopK)':>22}{'骨架':>6}{'详情':>6}{'索引':>6}{'TopK':>7}{'历史':>6}{'总输入':>8}  状态"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        flag = "⚠️ 超 800" if r["over_budget"] else ("✅" if r["system_base"] <= 800 else "✗")
        print(f"{r['mode']:<6}{r['system_base']:>22}{r['skeleton']:>6}{r['detail_tok']:>6}"
              f"{r['index']:>6}{r['topk']:>7}{r['history']:>6}{r['total_input']:>8}  {flag}"
              f"   [{r['note']}]")

    print("\n### 瘦身前后对比（system prompt 不含 TopK）\n")
    hdr2 = f"{'模式':<6}{'场景':<22}{'瘦身前':>8}{'瘦身后':>8}{'节省':>8}{'降幅':>8}"
    print(hdr2)
    print("-" * len(hdr2))
    for r in rows:
        print(f"{r['mode']:<6}{r['note']:<22}{r['base_system']:>8}{r['system_base']:>8}"
              f"{r['saved']:>8}{str(r['saved_pct']) + '%':>8}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", help="打印该模式的完整 prompt")
    ap.add_argument("--json", help="导出 JSON 到指定路径")
    args = ap.parse_args()

    rows = run(args.show)
    print_table(rows)

    over = [r for r in rows if r["over_budget"]]
    normal = [r for r in rows if "常态轮" in r["note"]]
    print(f"\n常态轮 system(不含TopK) 最大 {max((r['system_base'] for r in normal), default=0)} token "
          f"（上限 800）→ {'通过 ✅' if max((r['system_base'] for r in normal), default=0) <= 800 else '不通过 ❌'}")
    if over:
        print("超预算场景：" + ", ".join(f"{r['mode']}/{r['note']}" for r in over))

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)
        print(f"已导出 {args.json}")

    return 0 if not over else 1


if __name__ == "__main__":
    raise SystemExit(main())
