# -*- coding: utf-8 -*-
"""测试陪练 Agent - Gradio 入口层

4 个模式各占一个 gr.Tabs；每个 Tab 自带"记一下吧"（写入 saved_sessions）；
顶部"看看回忆"滑出右侧抽屉，卡片点击在只读弹层里回看完整对话，可单条删除。
chat_handler.py 一行不改——模式名直接传给它的 ui_mode 参数即可。
"""
import os
import sys
from datetime import datetime

import gradio as gr

from chat_handler import chat
import ui_theme

_DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "db")
if _DB_DIR not in sys.path:
    sys.path.insert(0, _DB_DIR)
from db import init_db, create_session, end_session, save_session, list_saved_sessions, get_saved_session, delete_saved_session

MODES = ["陪练模式", "速查模式", "出题模式", "执行模式"]
MODE_COLORS = {
    "陪练模式": "#6C63C7",
    "速查模式": "#10B981",
    "出题模式": "#F59E0B",
    "执行模式": "#EF4444",
}

MODE_CONFIG = {
    "陪练模式": {
        "placeholder": "输入你的问题…例如：SC296-WBA4 云台限位问题，帮我拆成六步定位路径",
        "hint": "默认不给答案，用六步引导陪你定位",
    },
    "速查模式": {
        "placeholder": "直接问用例内容，会给答案并附引用",
        "hint": "快速检索用例库，给出结论 + 引用用例 ID",
    },
    "出题模式": {
        "placeholder": "输入主题，让 AI 出题…例如：OTA 升级断电场景",
        "hint": "从用例库抽题，答对答错都反馈",
    },
    "执行模式": {
        "placeholder": "执行 1536949  /  跑门禁体检",
        "hint": "真机执行 + 离线门禁（需要 adb 连接和 skill_root 配置）",
    },
}

# 前端 JS：HTML 卡片没有后端事件，用隐藏 Textbox 做桥（点击卡片/删除 → 写值 → 触发 change）
_BRIDGE_JS = """
<script>
function mvFire(elemId, val) {
    var wrap = document.getElementById(elemId);
    if (!wrap) return;
    var ta = wrap.querySelector('textarea');
    if (!ta) return;
    var setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
    setter.call(ta, val);
    ta.dispatchEvent(new Event('input', {bubbles: true}));
    ta.dispatchEvent(new Event('change', {bubbles: true}));
}
document.addEventListener('click', function (e) {
    var del = e.target.closest('.saved-del');
    if (del) {
        e.stopPropagation();
        mvFire('mv-del-box', del.getAttribute('data-del-id'));
        return;
    }
    var card = e.target.closest('.saved-card');
    if (card) {
        mvFire('mv-open-box', card.getAttribute('data-saved-id'));
    }
}, true);
</script>
"""


def _mode_tag_html(mode: str) -> str:
    color = MODE_COLORS.get(mode, "#6B7280")
    return (f'<span style="display:inline-block;padding:2px 10px;border-radius:10px;'
            f'background:{color}15;color:{color};font-size:12px;font-weight:600;">{mode}</span>')


def _esc(s: str) -> str:
    return (s or "").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _format_time(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso)
        return dt.strftime("%m-%d %H:%M")
    except Exception:
        return iso[:16]


def _render_saved_list(filter_mode: str = "全部") -> str:
    """侧边栏列表 HTML（按时间倒序，每条是一张可点卡片，带删除 ✕）"""
    items = list_saved_sessions(filter_mode if filter_mode != "全部" else None)
    if not items:
        return ('<div style="padding:24px 12px;color:#9B95C7;font-size:13px;text-align:center;line-height:1.8;">'
                '还没有保存的对话<br/>聊两句后点"记一下吧"</div>')

    cards = []
    for it in items:
        ts = it["created_at"][:16].replace("T", " ")
        cards.append(
            f'<div data-saved-id="{it["id"]}" class="saved-card">'
            f'<div style="display:flex;align-items:center;gap:6px;margin-bottom:4px;">'
            f'{_mode_tag_html(it["mode"])}'
            f'<span style="margin-left:auto;color:#9B95C7;font-size:11px;">{ts}</span>'
            f'<span class="saved-del" data-del-id="{it["id"]}" title="删除这条收藏">✕</span>'
            f'</div>'
            f'<div style="color:#2E2A52;font-size:13px;line-height:1.4;">'
            f'{_esc(it["title"])}</div>'
            f'<div style="color:#9B95C7;font-size:11px;margin-top:2px;">'
            f'{it["msg_count"]} 条消息</div>'
            f'</div>'
        )
    return "\n".join(cards)


def _viewer_head_html(data: dict) -> str:
    """只读弹层顶部：模式标签 + 只读徽标 + 时间/条数 + 标题"""
    ts = data["created_at"][:16].replace("T", " ")
    return (
        '<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">'
        + _mode_tag_html(data["mode"])
        + '<span style="padding:2px 10px;border-radius:10px;background:rgba(140,132,200,.12);'
          'color:#6C63C7;font-size:12px;font-weight:600;">🔒 只读视图</span>'
        + f'<span style="margin-left:auto;color:#9B95C7;font-size:12px;">'
          f'{ts} · {data["msg_count"]} 条消息</span></div>'
        f'<div style="font-size:15px;font-weight:700;color:#2E2A52;margin-top:10px;">'
        f'{_esc(data["title"])}</div>'
    )


def _probe_health() -> str:
    def _dot(ok: bool, label: str) -> str:
        color = "#22C55E" if ok else "#EF4444"
        return f'<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:{color};margin-right:6px;"></span>{label}'
    check_ok = run_ok = map_ok = False
    try:
        from tools.registry import get_registry
        hc = get_registry().health_check()
        check_ok = hc.get("check_anchors", False)
        run_ok = hc.get("run_case", False)
        map_ok = hc.get("mapping_ready", False)
    except Exception:
        pass
    return (
        '<div style="padding:8px 14px;background:rgba(255,255,255,0.7);border-radius:8px;'
        'border:1px solid rgba(140,132,200,0.18);font-size:13px;color:#4B4580;margin-bottom:8px;">'
        f'{_dot(check_ok, "离线门禁")}  {_dot(run_ok, "真机 adb")}  {_dot(map_ok, "映射表就绪")}</div>'
    )


def _quiz_progress_html() -> str:
    try:
        conn = init_db()
        total = conn.execute("SELECT COUNT(*) FROM sessions WHERE mode='出题模式'").fetchone()[0]
        turns = conn.execute("SELECT COALESCE(SUM(total_turns),0) FROM sessions WHERE mode='出题模式'").fetchone()[0]
        if total > 0:
            return f'<div style="padding:8px 14px;background:rgba(255,255,255,0.7);border-radius:8px;border:1px solid rgba(140,132,200,0.18);font-size:13px;color:#4B4580;margin-bottom:8px;">📝 已完成 <b>{total}</b> 轮出题，累计 <b>{turns}</b> 题</div>'
    except Exception:
        pass
    return '<div style="padding:8px 14px;background:rgba(255,255,255,0.7);border-radius:8px;border:1px solid rgba(140,132,200,0.18);font-size:13px;color:#4B4580;margin-bottom:8px;">📝 说一个主题，我给你出题</div>'


def build_app() -> gr.Blocks:
    init_db()
    case_count = ui_theme.probe_case_count()
    try:
        from config import LLM_MODEL
        model = LLM_MODEL
    except Exception:
        model = ""

    with gr.Blocks(title="测试陪练 Agent") as demo:
        # ---- 顶部横幅 + "看看回忆" 按钮 ----
        with gr.Row(equal_height=False):
            with gr.Column(scale=10):
                gr.HTML(ui_theme.banner_html(), elem_id="mv-banner")
            with gr.Column(scale=1, min_width=100):
                sidebar_btn = gr.Button("📚 看看回忆", elem_id="mv-sidebar-btn",
                                        elem_classes=["sidebar-trigger"],
                                        size="sm")

        # ---- 主对话区（全宽；回忆抽屉是 fixed 浮层，不占布局）----
        with gr.Column(elem_id="mv-main"):
            with gr.Tabs(elem_id="mv-tabs"):

                # --- 陪练 ---
                with gr.TabItem("陪练模式", id="tab-p"):
                    sid_p = gr.State(None)
                    chatbot_p = gr.Chatbot(elem_id="mv-chat-p", height=480,
                                           placeholder=MODE_CONFIG["陪练模式"]["placeholder"], show_label=False)
                    msg_p = gr.Textbox(placeholder=MODE_CONFIG["陪练模式"]["placeholder"],
                                       container=False, scale=8, elem_id="mv-input-p")
                    with gr.Row():
                        save_p = gr.Button("📌 记一下吧", size="sm", min_width=110)
                        clear_p = gr.Button("🗑️ 清空本页对话", size="sm", min_width=120)
                        gr.HTML(
                            f'<span style="color:#9B95C7;font-size:12px;margin-left:auto;align-self:center;">'
                            f'{MODE_CONFIG["陪练模式"]["hint"]}</span>'
                        )
                    msg_p.submit(lambda t, h, s: _tab_submit(t, "陪练模式", h, s),
                                 [msg_p, chatbot_p, sid_p], [msg_p, chatbot_p, sid_p])
                    clear_p.click(lambda h, s: _tab_clear(h, s), [chatbot_p, sid_p], [chatbot_p, sid_p])
                    save_p.click(lambda h: _tab_save("陪练模式", h), [chatbot_p], None)

                # --- 速查 ---
                with gr.TabItem("速查模式", id="tab-q"):
                    sid_q = gr.State(None)
                    chatbot_q = gr.Chatbot(elem_id="mv-chat-q", height=480,
                                           placeholder=MODE_CONFIG["速查模式"]["placeholder"], show_label=False)
                    msg_q = gr.Textbox(placeholder=MODE_CONFIG["速查模式"]["placeholder"],
                                       container=False, scale=8, elem_id="mv-input-q")
                    with gr.Row():
                        save_q = gr.Button("📌 记一下吧", size="sm", min_width=110)
                        clear_q = gr.Button("🗑️ 清空本页对话", size="sm", min_width=120)
                    msg_q.submit(lambda t, h, s: _tab_submit(t, "速查模式", h, s),
                                 [msg_q, chatbot_q, sid_q], [msg_q, chatbot_q, sid_q])
                    clear_q.click(lambda h, s: _tab_clear(h, s), [chatbot_q, sid_q], [chatbot_q, sid_q])
                    save_q.click(lambda h: _tab_save("速查模式", h), [chatbot_q], None)

                # --- 出题 ---
                with gr.TabItem("出题模式", id="tab-z"):
                    sid_z = gr.State(None)
                    progress_html = gr.HTML(_quiz_progress_html(), elem_id="mv-progress")
                    chatbot_z = gr.Chatbot(elem_id="mv-chat-z", height=440,
                                           placeholder=MODE_CONFIG["出题模式"]["placeholder"], show_label=False)
                    msg_z = gr.Textbox(placeholder=MODE_CONFIG["出题模式"]["placeholder"],
                                       container=False, scale=8, elem_id="mv-input-z")
                    with gr.Row():
                        save_z = gr.Button("📌 记一下吧", size="sm", min_width=110)
                        clear_z = gr.Button("🗑️ 清空本页对话", size="sm", min_width=120)
                    msg_z.submit(lambda t, h, s: _tab_submit(t, "出题模式", h, s),
                                 [msg_z, chatbot_z, sid_z], [msg_z, chatbot_z, sid_z])
                    clear_z.click(lambda h, s: _tab_clear(h, s), [chatbot_z, sid_z], [chatbot_z, sid_z])
                    save_z.click(lambda h: _tab_save("出题模式", h), [chatbot_z], None)

                # --- 执行 ---
                with gr.TabItem("执行模式", id="tab-e"):
                    sid_e = gr.State(None)
                    status_html = gr.HTML(_probe_health(), elem_id="mv-status")
                    chatbot_e = gr.Chatbot(elem_id="mv-chat-e", height=440,
                                           placeholder=MODE_CONFIG["执行模式"]["placeholder"], show_label=False)
                    msg_e = gr.Textbox(placeholder=MODE_CONFIG["执行模式"]["placeholder"],
                                       container=False, scale=8, elem_id="mv-input-e")
                    with gr.Row():
                        save_e = gr.Button("📌 记一下吧", size="sm", min_width=110)
                        clear_e = gr.Button("🗑️ 清空本页对话", size="sm", min_width=120)
                    msg_e.submit(lambda t, h, s: _tab_submit(t, "执行模式", h, s),
                                 [msg_e, chatbot_e, sid_e], [msg_e, chatbot_e, sid_e])
                    clear_e.click(lambda h, s: _tab_clear(h, s), [chatbot_e, sid_e], [chatbot_e, sid_e])
                    save_e.click(lambda h: _tab_save("执行模式", h), [chatbot_e], None)

        # ---- 底部 ----
        gr.HTML(ui_theme.footer_html(case_count=case_count, model=model), elem_id="mv-foot-wrap")

        # ========== 浮层：回忆抽屉 + 只读查看器（CSS position:fixed，不占主区布局） ==========

        # 抽屉遮罩（点击等于点关闭按钮）
        drawer_backdrop = gr.HTML(
            '<div class="mv-backdrop-fixed" '
            'onclick="document.querySelector(\'#mv-drawer-close button\').click()"></div>',
            elem_id="mv-backdrop", visible=False,
        )
        # 查看器遮罩
        viewer_backdrop = gr.HTML(
            '<div class="mv-backdrop-fixed" '
            'onclick="document.querySelector(\'#mv-viewer-close button\').click()"></div>',
            elem_id="mv-viewer-backdrop", visible=False,
        )

        # ---- 右侧回忆抽屉 ----
        with gr.Column(visible=False, elem_id="mv-drawer") as drawer:
            with gr.Row():
                gr.HTML('<div class="mv-drawer-title">📚 我的回忆</div>')
                drawer_close = gr.Button("✕", size="sm", elem_id="mv-drawer-close", min_width=40)
            gr.HTML('<div style="color:#9B95C7;font-size:12px;margin:4px 0 8px;">'
                    '点卡片回看完整对话，✕ 可删除</div>')
            filter_dd = gr.Dropdown(choices=["全部"] + MODES, value="全部", label="按模式筛选",
                                    interactive=True, elem_id="mv-filter")
            saved_list_html = gr.HTML(_render_saved_list(), elem_id="mv-saved-list")

        # ---- 只读历史查看器（无输入框，天然不能发消息）----
        with gr.Column(visible=False, elem_id="mv-viewer") as viewer:
            viewer_head = gr.HTML("", elem_id="mv-viewer-head")
            viewer_chat = gr.Chatbot(elem_id="mv-viewer-chat", height=460,
                                     show_label=False, placeholder="")
            with gr.Row():
                gr.HTML('<span></span>')
                viewer_close = gr.Button("关闭", size="sm", elem_id="mv-viewer-close", min_width=100)

        # ---- HTML 卡片事件桥（Gradio 6 不挂载 visible=False 组件，
        #      故保持可见 + CSS 移出屏幕，JS 写值触发后端 change）----
        open_box = gr.Textbox(elem_id="mv-open-box", elem_classes=["mv-bridge-box"],
                              show_label=False, container=False)
        del_box = gr.Textbox(elem_id="mv-del-box", elem_classes=["mv-bridge-box"],
                             show_label=False, container=False)
        drawer_state = gr.State(False)

        # ========== 事件绑定 ==========

        def toggle_drawer(is_open: bool, filter_val: str):
            will_open = not bool(is_open)
            if will_open:
                return (gr.update(visible=True), gr.update(visible=True),
                        _render_saved_list(filter_val), True)
            return gr.update(visible=False), gr.update(visible=False), gr.update(), False

        def close_drawer():
            return gr.update(visible=False), gr.update(visible=False), False

        sidebar_btn.click(toggle_drawer, [drawer_state, filter_dd],
                          [drawer, drawer_backdrop, saved_list_html, drawer_state])

        drawer_close.click(close_drawer, None,
                           [drawer, drawer_backdrop, drawer_state])

        filter_dd.change(lambda fv: _render_saved_list(fv), [filter_dd], saved_list_html)

        # "记一下吧"后同步刷新抽屉列表（保持当前筛选）
        for _btn in (save_p, save_q, save_z, save_e):
            _btn.click(lambda fv: _render_saved_list(fv), [filter_dd], saved_list_html)

        def open_saved(raw: str):
            raw = (raw or "").strip()
            if not raw:
                return gr.update(visible=False), gr.update(visible=False), gr.update(), gr.update(), ""
            try:
                data = get_saved_session(int(raw))
            except (TypeError, ValueError):
                data = None
            if not data:
                return gr.update(visible=False), gr.update(visible=False), gr.update(), gr.update(), ""
            return (gr.update(visible=True), gr.update(visible=True),
                    _viewer_head_html(data), data["messages"], "")

        open_box.change(open_saved, [open_box],
                        [viewer, viewer_backdrop, viewer_head, viewer_chat, open_box])

        def on_delete(raw: str, filter_val: str):
            raw = (raw or "").strip()
            if raw:
                try:
                    delete_saved_session(int(raw))
                    gr.Info("已删除这条收藏")
                except (TypeError, ValueError):
                    pass
            return _render_saved_list(filter_val), ""

        del_box.change(on_delete, [del_box, filter_dd], [saved_list_html, del_box])

        viewer_close.click(lambda: (gr.update(visible=False), gr.update(visible=False)),
                           None, [viewer, viewer_backdrop])

    return demo


# ============== 公共 submit / clear / save ==============

def _tab_submit(user_text: str, mode: str, history: list | None, sid: int | None):
    if not user_text or not user_text.strip():
        return "", history or [], sid
    history = history or []
    if sid is None:
        sid = create_session(initial_mode=mode)
    # history 只传最近 5 轮（chat_handler 内部再裁一次，双保险）
    result = chat(user_text, mode, session_id=sid, history=history, _return_dict=True)
    history.append({"role": "user", "content": user_text})
    history.append({"role": "assistant", "content": result["reply"]})
    _log_prompt_tokens(mode, result)
    return "", history, sid


def _log_prompt_tokens(mode: str, result: dict):
    """把本轮 prompt 的 token 占用打到 stderr（可用 PROMPT_LOG=0 关闭）"""
    if os.environ.get("PROMPT_LOG", "1") == "0":
        return
    dbg = result.get("prompt_debug") or {}
    bd = dbg.get("breakdown") or {}
    if not bd:
        return
    flag = " ⚠️OVER" if dbg.get("over_budget") else ""
    print(
        f"[prompt] {mode} 骨架={bd.get('skeleton')} 详情={bd.get('details')}"
        f" 索引={bd.get('module_index')} | system(不含TopK)={bd.get('system_base')}/800"
        f"{flag} | TopK={bd.get('topk')} 历史={bd.get('history')}"
        f" | 总输入={bd.get('total_input')}",
        file=sys.stderr,
    )


def _tab_clear(history: list | None, sid: int | None):
    if sid is not None:
        try:
            end_session(sid)
        except Exception:
            pass
    return [], None


def _tab_save(mode: str, history: list | None):
    """点"记一下吧"：空对话给轻提示，否则存库（纯本地写库，不依赖 LLM）。"""
    if not history:
        gr.Warning("还没有对话可以存")
        return
    sid = save_session(mode, history)
    if sid is None:
        gr.Warning("还没有对话可以存")
        return
    gr.Info(f"✅ 已保存这段{mode}对话（共 {len(history)} 条消息）")


if __name__ == "__main__":
    app = build_app()
    print("启动中... 访问地址: http://127.0.0.1:7860")
    app.launch(
        server_name="127.0.0.1",
        server_port=7860,
        share=False,
        inbrowser=True,
        css=ui_theme.load_css(),
        theme=ui_theme.build_theme(),
        head=_BRIDGE_JS,
    )
