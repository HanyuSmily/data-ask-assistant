# -*- coding: utf-8 -*-
"""业务逻辑层：模式匹配 → prompt 组装 → 调 LLM → 返回回复
所有判断只写在这里，app.py 只传消息。"""
import os
import re
import sys
import time

from config import LLM_BASE_URL, LLM_API_KEY, LLM_MODEL, LLM_TIMEOUT
from llm import LLMClient, classify_error, LLM_EXCEPTIONS

PROMPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")

# 让 cases/ 子目录可 import
_CASES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cases")
if _CASES_DIR not in sys.path:
    sys.path.insert(0, _CASES_DIR)

# 让 db/ 子目录可 import
_DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "db")
if _DB_DIR not in sys.path:
    sys.path.insert(0, _DB_DIR)

# 让 tools/ 子目录可 import
_TOOLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools")
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

# 显式模式 → 提示词文件
MODE_PROMPT = {
    "陪练模式": "practice.md",
    "速查模式": "quickref.md",
    "出题模式": "quiz.md",
    "执行模式": "execute.md",
}

# 关键词 → 模式（兜底）
KEYWORD_MODE = [
    ("陪练", "陪练模式"),
    ("速查", "速查模式"),
    ("出题", "出题模式"),
    ("执行", "执行模式"),
]
DEFAULT_MODE = "速查模式"

# 从 Agent 回复里提取引用的 case_id
_CASE_ID_PATTERN = re.compile(r"(?:用例|case|用例ID|case_id)\s*[:：#\-]?\s*(\d{5,8})", re.IGNORECASE)
# 从用户输入里提取 case_id（更宽松：独立的 5-8 位数字）
_USER_CASE_ID_PATTERN = re.compile(r"\b(\d{5,8})\b")

# 全局单例
_client: LLMClient | None = None


def _get_client() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient(
            base_url=LLM_BASE_URL,
            api_key=LLM_API_KEY,
            model=LLM_MODEL,
            timeout=LLM_TIMEOUT,
        )
    return _client


def _detect_mode(user_text: str, ui_mode: str | None) -> str:
    """优先用 UI 选的模式；UI 没传就关键词匹配；都没有走兜底"""
    if ui_mode in MODE_PROMPT:
        return ui_mode
    for kw, mode in KEYWORD_MODE:
        if kw in user_text:
            return mode
    return DEFAULT_MODE


def _get_retrieval_conn():
    """懒加载 retrieval SQLite 连接（单例）"""
    global _retrieval_conn
    if '_retrieval_conn' not in globals() or _retrieval_conn is None:
        from retrieval.db import get_conn
        import config as _cfg
        _retrieval_conn = get_conn(_cfg.RETR_DB_PATH)
    return _retrieval_conn


def _dynamic_case_library(user_text: str) -> str:
    """运行时从 SQLite 动态检索相关用例 → 拼装注入 prompt 片段"""
    try:
        from retrieval.retriever import retrieve
        from retrieval.injector import inject
        from retrieval.db import format_module_index_for_prompt
        import config as _cfg

        conn = _get_retrieval_conn()

        # 1. 模块索引常驻（轻量，每次读一次）
        module_index_text = format_module_index_for_prompt(conn)

        # 2. 动态检索 TopK
        result = retrieve(conn, user_text)
        injected = inject(result["final"])

        return f"{module_index_text}\n\n{injected}"
    except Exception as e:
        return f"(用例库动态检索失败: {e})"


# 兼容旧接口：无参数时返回模块索引（给系统提示词常驻用）
def _load_cases_index() -> str:
    """加载清洗后的用例库：模块索引常驻 + 动态检索由 _dynamic_case_library 处理"""
    try:
        from retrieval.db import format_module_index_for_prompt, init_db, seed_samples
        import config as _cfg
        from pathlib import Path
        db_path = Path(_cfg.RETR_DB_PATH)
        if not db_path.exists():
            conn = init_db(str(db_path))
            seed_samples(conn)
            from retrieval.db import rebuild_fts, update_module_index
            rebuild_fts(conn)
            update_module_index(conn)
        conn = _get_retrieval_conn()
        return format_module_index_for_prompt(conn)
    except Exception as e:
        return f"(用例库加载失败: {e})"


def _load_prompt(filename: str) -> str:
    path = os.path.join(PROMPTS_DIR, filename)
    with open(path, encoding="utf-8") as f:
        prompt = f.read()
    # 系统提示词里只留模块索引（常驻轻量）；动态 TopK 在 chat() 里单独注入
    return prompt.replace("{CASE_LIBRARY}", _load_cases_index())


def chat(user_text: str, ui_mode: str | None = None, session_id: int | None = None,
         _return_dict: bool = False) -> str | dict:
    """入口函数

    Args:
        user_text: 用户输入
        ui_mode:   UI Radio 传过来的模式名
        session_id: 成长档案 session（可 None，不写库）
        _return_dict: True 时返回 dict 含埋点（tokens / case_ids / latency）；
                      False 时只返回 reply 字符串（兼容旧调用）

    Returns:
        str（默认）或 dict: {reply, prompt_name, tokens_input, tokens_output,
                            tokens_total, latency_ms, case_ids_referenced,
                            reply_type, is_success}
    """
    result: dict = {
        "reply": "", "prompt_name": "",
        "tokens_input": 0, "tokens_output": 0, "tokens_total": 0,
        "latency_ms": 0, "case_ids_referenced": [],
        "reply_type": "", "is_success": 0, "mode": "",
        "retrieval_info": None,
    }

    if not user_text or not user_text.strip():
        result["reply"] = ""
        return result if _return_dict else ""

    mode = _detect_mode(user_text, ui_mode)
    result["mode"] = mode

    # ---- 执行模式走工具链（跳过普通 LLM 回复路径）----
    if mode == "执行模式":
        return _handle_execute_mode(user_text, result, session_id, _return_dict)

    # 1) 加载 prompt（只有模块索引，动态 TopK 在下面注入）
    try:
        prompt_file = MODE_PROMPT[mode]
        system_prompt = _load_prompt(prompt_file)
        result["prompt_name"] = prompt_file
    except Exception as e:
        result["reply"] = f"[提示词加载失败] 模式={mode} 文件={prompt_file} err={e}"
        return result if _return_dict else result["reply"]

    # 2) 动态检索 TopK → 拼成检索上下文，追加到 system_prompt 末尾
    retrieval_ctx = ""
    try:
        from retrieval.retriever import retrieve
        from retrieval.injector import inject
        conn = _get_retrieval_conn()
        r = retrieve(conn, user_text)
        retrieval_ctx = inject(r["final"])
        result["retrieval_info"] = {
            "tokens": r["tokens"],
            "module_hits": r["module_hits"],
            "fts_count": r["fts_count"],
            "final_count": r["final_count"],
        }
    except Exception:
        pass  # 检索失败不阻断

    if retrieval_ctx:
        system_prompt = system_prompt.rstrip() + "\n\n" + retrieval_ctx

    # 3) 调 LLM（计时 + token 估算）
    t0 = time.time()
    try:
        answer = _get_client().chat(system_prompt, user_text)
        result["is_success"] = 1
    except LLM_EXCEPTIONS as e:
        answer = classify_error(e)
    except Exception as e:
        answer = f"[LLM 调用异常] {type(e).__name__}: {e}"

    result["latency_ms"] = int((time.time() - t0) * 1000)
    result["reply"] = answer

    # 4) 埋点：估算 token + 提取 case_id + 判断 reply_type
    zh = sum(1 for c in system_prompt + user_text if '\u4e00' <= c <= '\u9fff')
    other = sum(1 for c in system_prompt + user_text if not ('\u4e00' <= c <= '\u9fff'))
    result["tokens_input"] = int(zh * 1.5 + other * 0.25)
    zh2 = sum(1 for c in answer if '\u4e00' <= c <= '\u9fff')
    other2 = sum(1 for c in answer if not ('\u4e00' <= c <= '\u9fff'))
    result["tokens_output"] = int(zh2 * 1.5 + other2 * 0.25)
    result["tokens_total"] = result["tokens_input"] + result["tokens_output"]

    result["case_ids_referenced"] = list(set(_CASE_ID_PATTERN.findall(answer)))

    result["reply_type"] = _classify_reply(mode, answer)

    # 5) 写成长档案（session_id 非 None 时）
    if session_id is not None:
        _archive(session_id, user_text, result)

    return result if _return_dict else result["reply"]


def _classify_reply(mode: str, reply: str) -> str:
    """粗分类 Agent 回复类型（供周统计用）"""
    if mode == "陪练模式":
        if "？" in reply or "?" in reply:
            return "引导反问"
        return "直接作答"
    if mode == "出题模式":
        if any(kw in reply for kw in ("评分", "得分", "分数", "错漏")):
            return "评分反馈"
        return "给出题"
    return "直接作答"


def _archive(session_id: int, user_text: str, result: dict):
    """把一轮对话写入 SQLite（失败静默，不阻断主流程）"""
    try:
        import sys as _sys
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "db"))
        from db import insert_user_message, insert_assistant_message
        insert_user_message(session_id, result["mode"], user_text)
        insert_assistant_message(
            session_id=session_id,
            mode_at_send=result["mode"],
            content=result["reply"],
            prompt_name=result["prompt_name"],
            model_used=LLM_MODEL,
            tokens_input=result["tokens_input"],
            tokens_output=result["tokens_output"],
            tokens_total=result["tokens_total"],
            latency_ms=result["latency_ms"],
            case_ids_referenced=result["case_ids_referenced"],
            reply_type=result["reply_type"],
            is_success=result["is_success"],
        )
    except Exception as e:
        # 归档失败不影响对话本身
        import sys as _sys2
        print(f"[archive warn] {e}", file=_sys2.stderr)


# ==========================================================================
# 执行模式工具链
# ==========================================================================

def _handle_execute_mode(user_text: str, result: dict, session_id, _return_dict) -> str | dict:
    """执行模式：用户说「执行 1536949」→ 检查白名单 → 查 case_mapping → 确认门 → 框架执行 → 比对 → 不一致转陪练"""
    from registry import get_registry
    from tool_types import ExpectedPlan, CaseStatus

    reg = get_registry()

    # 0) 框架健康检查（分项，离线门禁 vs 真机分别提示）
    hc = reg.health_check()
    check_anchors_ok = hc["check_anchors"]
    run_case_ok = hc["run_case"]
    mapping_ok = hc["mapping_ready"]

    if not check_anchors_ok and not run_case_ok:
        result["reply"] = (
            "⚠️ 执行框架当前不可用（离线门禁 + 真机都没就绪）。\n"
            "请检查 config.yaml 里 tool.skill_root 是否填了真实路径，\n"
            "或确认 adb devices 能看到手机。"
        )
        result["reply_type"] = "框架不可用"
        return _finalize(result, session_id, _return_dict)

    # 1) 提取 case_id
    case_id = _extract_case_id(user_text)
    # 离线门禁请求（不需要 case_id）
    if not case_id and any(kw in user_text for kw in ("门禁", "check_anchors", "check", "体检")):
        if not check_anchors_ok:
            result["reply"] = "⚠️ 离线门禁脚本未就绪（skill_root 未配置或脚本不存在）。"
            result["reply_type"] = "离线门禁不可用"
            return _finalize(result, session_id, _return_dict)
        return _do_check_anchors(reg, result, session_id, _return_dict)

    if not case_id:
        result["reply"] = (
            "请告诉我要执行的用例 ID（5-8 位数字，例 `执行 1536949`），"
            "或告诉我 `跑门禁体检`。"
        )
        return _finalize(result, session_id, _return_dict)

    # 2) 查 case_mapping：tms_case_id → skill cases/*.json 路径
    case_path = reg.mapping.get_path(case_id)
    if case_path is None:
        # 没映射：提示用户用 LLM 生成草稿（但不自动生效）
        result["reply"] = (
            f"⚠️ case_mapping 里没有 `{case_id}` 的映射。\n"
            f"TMS 数字 ID 和 skill 的 cases/*.json 是两套体系，所有映射必须人工确认。\n\n"
            f"你可以让我**生成草稿**（回复「生成映射草稿 {case_id}」），"
            f"我会从 cases_clean.json 里查这条用例的信息，"
            f"并建议 skill 里哪个 cases/*.json 可能对应它；"
            f"草稿不会自动写入 case_mapping.json，需你确认。"
        )
        result["reply_type"] = "缺 case_mapping"
        return _finalize(result, session_id, _return_dict)

    # 3) 用户点了「确认执行」按钮？
    if "确认执行" in user_text or user_text.strip().lower() in ("确认", "ok", "yes", "y"):
        return _do_execute(case_id, case_path, reg, result, session_id, _return_dict)

    # 4) 用户点了「取消」
    if any(kw in user_text for kw in ("取消", "cancel", "算了", "stop")):
        reg.cancel_confirmation(case_id)
        result["reply"] = "已取消。"
        return _finalize(result, session_id, _return_dict)

    # 5) 真机没就绪 → 只允许跑离线门禁
    if not run_case_ok:
        result["reply"] = (
            f"⚠️ 真机执行未就绪（adb 没看到手机或 runner.py 不可用）。\n"
            f"可以先跑离线门禁体检（回复「跑门禁体检」）。\n\n"
            f"等设备就绪后再执行 `{case_id}`。"
        )
        result["reply_type"] = "真机未就绪"
        return _finalize(result, session_id, _return_dict)

    # 6) 第一次请求执行 → 走确认门
    allowed, reason = reg.whitelist.can_run(case_id)
    if not allowed:
        result["reply"] = f"⛔ 用例 {case_id} 不允许自动执行（{reason}）。\n如需执行请手动触发框架。"
        result["reply_type"] = "被白名单拦截"
        return _finalize(result, session_id, _return_dict)

    expected_plan = _build_expected_plan(case_id)
    expected_status = expected_plan.expected_status.value if expected_plan else "PASS"

    ctx = {"reason": f"用户请求执行，Agent 预期 {expected_status}（用例摘要见 LLM 预判）",
           "case_path": case_path}
    prompt_text = reg.request_confirmation(case_id, ctx)

    if expected_plan and expected_plan.key_assertions:
        prompt_text += f"\n\n**Agent 预判要点**：{', '.join(expected_plan.key_assertions[:3])}"
    prompt_text += f"\n\n**skill 路径**：`{case_path}`"

    result["reply"] = prompt_text
    result["reply_type"] = "等待确认"
    reg._pending_confirmations[case_id]["expected_plan"] = expected_plan
    reg._pending_confirmations[case_id]["case_path"] = case_path
    return _finalize(result, session_id, _return_dict)


def _do_execute(case_id: str, case_path: str, reg, result: dict, session_id, _return_dict) -> str | dict:
    """用户点了确认 → 真机执行 + 比对 + 不一致转陪练"""
    from registry import get_registry
    reg2 = get_registry()
    ctx = reg2._pending_confirmations.get(str(case_id), {})
    expected_plan = ctx.get("expected_plan")

    if not expected_plan:
        result["reply"] = "⚠️ 没找到对应的执行上下文，请重新发起执行请求。"
        return _finalize(result, session_id, _return_dict)

    if reg2.run_case is None:
        result["reply"] = "⚠️ run_case adapter 未配置（config.yaml tool.skill_root 为空）。"
        return _finalize(result, session_id, _return_dict)

    # 1) 真机执行
    actual = reg2.run_case.execute(case_id, params={"case_path": case_path})
    if actual is None:
        result["reply"] = "执行失败：adapter 返回空结果。"
        return _finalize(result, session_id, _return_dict)

    # 2) 比对
    compare = reg2.run_case.compare(expected_plan, actual)

    # 3) 构建回复
    lines = [f"## 执行报告 · 用例 {actual.case_id}（路径 `{case_path}`）"]
    status_icon = "✅" if compare.status_match else "❌"
    lines.append(f"{status_icon} 预期 {expected_plan.expected_status.value} → 实际 {actual.status.value}")
    lines.append(f"⏱️ 耗时：{actual.duration_ms}ms  {'✅' if compare.duration_match else '⚠️ 偏差较大'}")

    if compare.total_steps > 0:
        lines.append(f"📝 步骤一致：{compare.consistent_steps}/{compare.total_steps}")

    if compare.mismatches:
        lines.append(f"\n**不一致清单**：")
        for m in compare.mismatches[:5]:
            sev_icon = "🔴" if m.severity == "high" else "🟡"
            lines.append(f"  {sev_icon} 步骤{m.index} {m.field}: 预期「{m.expected[:40]}」→ 实际「{m.actual[:40]}」")
        if len(compare.mismatches) > 5:
            lines.append(f"  ... 还有 {len(compare.mismatches) - 5} 处，省略。")

    # 4) 比对不一致 → 自动转陪练
    if compare.suggest_practice:
        lines.append(f"\n---\n🔄 **自动切换陪练模式**\n")
        lines.append("检测到执行结果与预期不符，切换到陪练模式帮你分析原因。")
        lines.append(f"\n陪练问题：\n> 用例 {actual.case_id} 执行结果 {actual.status.value}（Agent 预期 {expected_plan.expected_status.value}），{compare.summary}。")
        result["reply"] = "\n".join(lines)
        result["reply_type"] = "执行不匹配转陪练"
        result["case_ids_referenced"] = [actual.case_id]
        return _finalize(result, session_id, _return_dict)

    result["reply"] = "\n".join(lines) + "\n\n✅ 执行结果与预期完全一致，Agent 预判准确。"
    result["reply_type"] = "执行一致"
    result["case_ids_referenced"] = [actual.case_id]
    return _finalize(result, session_id, _return_dict)


def _do_check_anchors(reg, result: dict, session_id, _return_dict) -> str | dict:
    """离线门禁体检（无真机）"""
    if reg.check_anchors is None:
        result["reply"] = "⚠️ check_anchors adapter 未配置。"
        return _finalize(result, session_id, _return_dict)

    actual = reg.check_anchors.execute("(离线门禁)")
    lines = ["## 离线门禁体检报告"]
    icon = "✅" if actual.status.value == "PASS" else "❌"
    lines.append(f"{icon} 硬错误：{actual.status.value}")
    if actual.error:
        lines.append(f"\n**stderr 摘要**：\n```\n{actual.error}\n```")
    # 建议用户去看完整报告
    lines.append("\n📄 完整报告在 `ipc-panel-case-builder/scripts/panel_map/ANCHOR_COVERAGE.md`")

    result["reply"] = "\n".join(lines)
    result["reply_type"] = "离线门禁"
    return _finalize(result, session_id, _return_dict)


def _build_expected_plan(case_id: str):
    """用 LLM 先模拟执行一条用例，生成 ExpectedPlan（执行前预判）"""
    from tool_types import ExpectedPlan, CaseStatus, ExpectedStep

    # 从本地用例库里查这条用例（有就塞给 LLM）
    case_json = _lookup_case_in_library(case_id)
    if not case_json:
        return ExpectedPlan(case_id=str(case_id))

    # 构造一个"请预判这条用例怎么跑"的 prompt，拿 LLM 输出
    # 简化版：直接填 ExpectedPlan，不开第二轮 LLM 调用（省 token 和延迟）
    steps_text = case_json.get("step", "")
    expected_text = case_json.get("expect", "")
    expected_steps = []
    if steps_text:
        for i, line in enumerate([l.strip() for l in steps_text.splitlines() if l.strip()], 1):
            expected_steps.append(ExpectedStep(index=i, action=line, expected=""))
    if expected_text:
        # 把预期文本附加到最后一步
        if expected_steps:
            expected_steps[-1].expected = expected_text
        else:
            expected_steps.append(ExpectedStep(index=1, action="", expected=expected_text))

    # 优先级高的用例更可能 PASS（保守预判）
    prio = case_json.get("priority", "")
    expected_duration = 60000 if prio == "P0" else 30000

    return ExpectedPlan(
        case_id=str(case_id),
        expected_status=CaseStatus.PASS,  # 默认预期 PASS，失败了才有意思
        expected_duration_ms=expected_duration,
        expected_steps=expected_steps,
        key_assertions=_extract_assertion_keywords(expected_text),
    )


def _lookup_case_in_library(case_id: str) -> dict | None:
    """从 cases_clean.json 查单条"""
    try:
        import json as _json
        import sys as _sys
        _sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "cases"))
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cases", "cases_clean.json")
        with open(p, encoding="utf-8") as f:
            for c in _json.load(f):
                if str(c.get("case_id")) == str(case_id):
                    return c
    except Exception:
        pass
    return None


def _extract_assertion_keywords(expected_text: str) -> list[str]:
    """从预期文本里抽几个关键词当关键断言"""
    text = expected_text or ""
    # 简单：抽带引号/书名号的词组，或数字+单位
    import re as _re
    quoted = _re.findall(r"[「」""''《》]?([\u4e00-\u9fffA-Za-z0-9]+(?:秒|分钟|次|条|个|步|帧|MB|KB|ms|s))[」」""''》]?", text)
    return quoted[:3]


def _extract_case_id(user_text: str) -> str | None:
    """从用户文本里拿 case_id（优先显式关键词，其次独立数字）"""
    m = _CASE_ID_PATTERN.search(user_text)
    if m:
        return m.group(1)
    m = _USER_CASE_ID_PATTERN.search(user_text)
    if m:
        # 只接受 15xxxxxx 系列（项目里的 case_id 格式），避免把版本号/IP 误判
        val = m.group(1)
        if val.startswith(("153", "154", "155")):
            return val
        return val  # 先放开，后续按需收紧
    return None


def _finalize(result: dict, session_id, _return_dict):
    """统一收尾：埋点 + 归档 + 返回"""
    # 执行模式里没有 system_prompt，token 估算走 0（执行前后的 LLM 预判单独算）
    result["tokens_total"] = result.get("tokens_total", 0)
    if session_id is not None:
        _archive(session_id, "", result)  # user_text 在执行模式里没传，归档不完整也没关系
    return result if _return_dict else result["reply"]
