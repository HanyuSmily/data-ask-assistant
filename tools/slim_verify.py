# -*- coding: utf-8 -*-
"""瘦身验收脚本：骨架预算 + 历史窗口 + 真实 5 轮陪练行为。

用法：
    python tools/slim_verify.py            # 全量验收（会真实调用 LLM）
    python tools/slim_verify.py --offline  # 只跑本地检查，不调 LLM
"""
from __future__ import annotations

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)

import chat_handler as ch  # noqa: E402

# 骨架预算：模式 -> (上限, 说明)
BUDGET = {
    "陪练模式": (500, "≤500 token（约 300 汉字）"),
    "速查模式": (300, "≤300 token"),
    "出题模式": (300, "≤300 token"),
    "执行模式": (300, "≤300 token"),
}

# 5 轮真实陪练对话（刻意信息不全，检验是否持续反问而非给答案）
ROUNDS = [
    "摄像头晚上画面全黑，白天正常",
    "指示灯是红的常亮，晚上就是看不见东西",
    "固件版本是 3.2.1，App 是最新的",
    "换到另一个房间也一样黑",
    "我不知道下一步该查什么了",
]


def check_skeleton_budget() -> bool:
    print("\n### 1. 骨架 prompt 预算\n")
    ok = True
    for mode, (cap, desc) in BUDGET.items():
        info = ch.build_prompt(mode, "占位")
        tok = info["breakdown"]["skeleton"]
        flag = "✅" if tok <= cap else "❌"
        if tok > cap:
            ok = False
        print(f"{mode:<6}{tok:>5} / {cap:<4} {flag}  {desc}")
    return ok


def check_history_window() -> bool:
    print("\n### 2. 历史窗口（应只保留最近 5 轮 = 10 条）\n")
    fake = []
    for i in range(7):
        fake.append({"role": "user", "content": f"第{i + 1}轮提问"})
        fake.append({"role": "assistant", "content": f"第{i + 1}轮回答"})
    trimmed = ch._trim_history(fake)
    ok = len(trimmed) == 10 and trimmed[0]["content"] == "第3轮提问"
    print(f"输入 14 条 → 保留 {len(trimmed)} 条，首条 = {trimmed[0]['content']!r} "
          f"{'✅' if ok else '❌'}")
    print(f"末条 = {trimmed[-1]['content']!r}")

    # 空/异常输入
    for bad in (None, [], [{"role": "system", "content": "x"}], [("user", "")]):
        assert ch._trim_history(bad) == [] or len(ch._trim_history(bad)) <= 1
    print("空值/异常输入处理 ✅")
    return ok


def check_no_duplicate_user() -> bool:
    """history 里已含本轮输入时不应重复追加"""
    print("\n### 3. messages 结构\n")
    hist = [{"role": "user", "content": "上一轮"},
            {"role": "assistant", "content": "上一轮回答"}]
    info = ch.build_prompt("陪练模式", "本轮问题", hist)
    msgs = info["messages"]
    roles = [m["role"] for m in msgs]
    ok = roles == ["system", "user", "assistant", "user"] and msgs[-1]["content"] == "本轮问题"
    print(f"role 序列 = {roles} {'✅' if ok else '❌'}")
    print(f"历史 token = {info['breakdown']['history']}")
    return ok


def _chat_with_retry(q: str, history: list, attempts: int = 3) -> dict:
    """LLM 偶发连接错误时重试（只重试网络类失败，不重试业务错误）"""
    import time
    last: dict = {}
    for i in range(attempts):
        last = ch.chat(q, "陪练模式", history=history, _return_dict=True)
        if last.get("is_success") == 1:
            return last
        if "Connection" not in str(last.get("reply", "")) and "Timeout" not in str(last.get("reply", "")):
            return last
        if i < attempts - 1:
            print(f"  ↻ 第 {i + 1} 次连接失败，2s 后重试")
            time.sleep(2)
    return last


def run_live_rounds() -> bool:
    print("\n### 4. 真实 5 轮陪练行为\n")
    history: list[dict] = []
    all_ask = True
    prev_ctx = 0
    for i, q in enumerate(ROUNDS, 1):
        result = _chat_with_retry(q, history)
        reply = result["reply"]
        dbg = result.get("prompt_debug") or {}
        bd = dbg.get("breakdown") or {}
        asks = ("？" in reply) or ("?" in reply)
        if not asks:
            all_ask = False
        print(f"\n--- 第 {i} 轮 ---")
        print(f"用户: {q}")
        print(f"回复: {reply[:180]}{'...' if len(reply) > 180 else ''}")
        print(f"[prompt] 骨架={bd.get('skeleton')} 详情={bd.get('details')}"
              f" 索引={bd.get('module_index')} system={bd.get('system_base')}/800"
              f" TopK={bd.get('topk')} 历史={bd.get('history')} 总输入={bd.get('total_input')}"
              f" | 反问={'是' if asks else '否'}")
        if bd.get("history", 0) < prev_ctx and i > 1:
            print("  ⚠️ 历史 token 异常下降")
        prev_ctx = bd.get("history", 0)

        history.append({"role": "user", "content": q})
        history.append({"role": "assistant", "content": reply})
        if result.get("is_success") != 1:
            print("  ⚠️ LLM 调用失败，无法判定行为")
            return False

    print(f"\n5 轮全部反问、不给答案 → {'✅ 通过' if all_ask else '❌ 存在直接作答'}")
    return all_ask


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="不调用 LLM")
    args = ap.parse_args()

    ok = True
    ok &= check_skeleton_budget()
    ok &= check_history_window()
    ok &= check_no_duplicate_user()
    if not args.offline:
        ok &= run_live_rounds()
    else:
        print("\n(offline 模式：跳过真实 5 轮对话)")

    print("\n" + ("=" * 50))
    print("验收结果：" + ("全部通过 ✅" if ok else "存在未通过项 ❌"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
