# -*- coding: utf-8 -*-
"""测试陪练 Agent - Gradio 入口层
职责：起本地 Web 页面，保留 Radio 模式选择，只绑定 chat_handler，不含业务逻辑。"""
import os
import sys

import gradio as gr

from chat_handler import chat

# 让 db/ 子目录可 import
_DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "db")
if _DB_DIR not in sys.path:
    sys.path.insert(0, _DB_DIR)
from db import init_db, create_session, end_session

MODES = ["陪练模式", "速查模式", "出题模式", "执行模式"]


def build_app() -> gr.Blocks:
    # 首次运行初始化 SQLite
    init_db()

    with gr.Blocks(title="测试陪练 Agent") as demo:
        gr.Markdown("# 🧪 测试陪练 Agent\n本地 Web 入口 · 三模式 + 成长档案")

        # 会话状态（用户看不到，只在服务端维护）
        session_id = gr.State(None)

        with gr.Row():
            mode = gr.Radio(
                choices=MODES,
                value="陪练模式",
                label="模式",
                interactive=True,
            )

        chatbot = gr.Chatbot(height=500)
        msg = gr.Textbox(placeholder="输入你的问题...", container=False, scale=7)

        def on_load(mode_val):
            """页面加载时创建新 session"""
            sid = create_session(initial_mode=mode_val)
            return sid

        def submit(user_text, mode_val, history, sid):
            if not user_text.strip():
                return "", history, sid
            history = history or []

            # 如果 session 还没创建（首次 submit 兜底）
            if sid is None:
                sid = create_session(initial_mode=mode_val)

            result = chat(user_text, mode_val, session_id=sid, _return_dict=True)
            history.append({"role": "user", "content": user_text})
            history.append({"role": "assistant", "content": result["reply"]})
            return "", history, sid

        msg.submit(submit, [msg, mode, chatbot, session_id], [msg, chatbot, session_id])

        def on_clear(history, sid):
            """清空调料时 end_session，下次 submit 自动开新 session"""
            if sid is not None:
                end_session(sid)
            return [], None

        with gr.Row():
            clear_btn = gr.Button("清空调料", size="sm")
            clear_btn.click(on_clear, [chatbot, session_id], [chatbot, session_id])

        # 页面加载时创建 session
        demo.load(on_load, [mode], [session_id])

    return demo


if __name__ == "__main__":
    app = build_app()
    print("启动中... 访问地址: http://127.0.0.1:7860")
    app.launch(server_name="127.0.0.1", server_port=7860, share=False, inbrowser=True)
