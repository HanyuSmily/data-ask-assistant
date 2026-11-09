# -*- coding: utf-8 -*-
"""临时预览实例：7861 端口，用于界面迭代（不干扰 7860 正式服务）"""
import ui_theme
from app import build_app

app = build_app()
app.launch(
    server_name="127.0.0.1",
    server_port=7861,
    share=False,
    inbrowser=False,
    css=ui_theme.load_css(),
    theme=ui_theme.build_theme(),
    quiet=True,
)
