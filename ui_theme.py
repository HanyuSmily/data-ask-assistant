# -*- coding: utf-8 -*-
"""界面层：HTML 片段 + Gradio 主题。

与 app.py 的业务逻辑完全解耦——这里只负责"长什么样"。
配色：蓝紫主色 + 莫兰迪渐变（详见 ui.css）。
"""
import os
from pathlib import Path

import gradio as gr

_HERE = Path(os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------- 主题
def build_theme() -> gr.Theme:
    """蓝紫主色主题（字体走本地，避免外网加载）。"""
    theme = gr.themes.Soft(
        primary_hue=gr.themes.colors.indigo,
        secondary_hue=gr.themes.colors.violet,
        neutral_hue=gr.themes.colors.slate,
        font=["Microsoft YaHei", "PingFang SC", "Helvetica Neue", "Arial", "sans-serif"],
    )
    theme.set(
        body_background_fill="#F4F2FB",
        body_text_color="#2E2A52",
        block_background_fill="rgba(255,255,255,0.72)",
        block_border_color="rgba(140,132,200,0.18)",
        input_background_fill="rgba(255,255,255,0.92)",
        input_border_color="rgba(140,132,200,0.24)",
        button_primary_background_fill="linear-gradient(135deg, #6C63C7, #8B7FC7)",
        button_primary_text_color="#FFFFFF",
        button_secondary_background_fill="linear-gradient(135deg, #8C82D8, #B0A8E4)",
        button_secondary_text_color="#FFFFFF",
        button_secondary_border_color="transparent",
    )
    return theme


def load_css() -> str:
    """读取 ui.css。"""
    return (_HERE / "ui.css").read_text(encoding="utf-8")


# ---------------------------------------------------------------- 顶部横幅
def banner_html() -> str:
    """顶部横幅：标题 + 副标题 + 状态胶囊（原界面信息全部保留）。"""
    return """
<div class="mv-in">
  <div class="mv-logo">🧪</div>
  <div class="mv-txt">
    <h1>测试陪练 Agent</h1>
    <div class="mv-sub">本地 Web 入口 · 三模式 + 成长档案</div>
  </div>
  <div class="mv-chips">
    <span class="mv-chip"><span class="dot"></span>服务就绪</span>
    <span class="mv-chip"><span class="dot amber"></span>本地 SQLite 成长档案</span>
    <span class="mv-chip"><span class="dot"></span>IPC 全量用例库</span>
  </div>
</div>
"""


# ---------------------------------------------------------------- 侧栏模式说明
def sidebar_desc_html() -> str:
    """四种模式的一句话说明（说明文字来自 prompts/ 下的模式定义）。"""
    items = [
        ("d1", "陪练模式", "六步定位法逐轮推进，不给答案，训练你独立分析与定位问题的能力。"),
        ("d2", "速查模式", "术语、方法、工具、平台操作速查：先给结论，再展开要点。"),
        ("d3", "出题模式", "考核式出题：给功能点出题 + 考核维度，你独立作答后给分反馈。"),
        ("d4", "执行模式", "按用例 ID 触发面板自动化框架，跑真实 IPC 用例并比对结果。"),
    ]
    html = ['<div class="mv-desc">']
    for cls, title, desc in items:
        html.append(
            f'<div class="mv-desc-item {cls}"><div><b>{title}</b><span>{desc}</span></div></div>'
        )
    html.append("</div>")
    return "\n".join(html)


# ---------------------------------------------------------------- 底部信息条
MODE_CARDS = [
    ("m1", "🎯 陪练模式", "六步定位法逐轮推进，默认不给答案，训练你独立分析与定位问题的能力。"),
    ("m2", "🔍 速查模式", "测试知识速查：先给一句话结论，再展开要点，单次回复不超过 400 字。"),
    ("m3", "📝 出题模式", "考核式出题：给功能点出题 + 考核维度，你独立作答后给分与反馈。"),
    ("m4", "⚡ 执行模式", "按用例 ID 触发面板自动化框架，跑真实 IPC 用例并比对执行结果。"),
]


def footer_html(case_count: int = 0, model: str = "", retrieval: str = "") -> str:
    """底部信息条：先列四种模式，再列引擎硬信息。"""
    engine = [
        ("e1", "用例库规模", f"{case_count:,} 条 IPC 用例" if case_count else "IPC 全量用例库"),
        ("e2", "检索策略", retrieval or "FTS5 BM25 + 目录双路召回 + 加权重排"),
        ("e3", "对话模型", model or "deepseek-v4.1-flash"),
        ("e4", "成长档案", "SQLite 会话留存，按模式沉淀训练轨迹"),
    ]
    html = ['<div class="mv-foot">']

    html.append('<div class="mv-foot-sec"><div class="mv-foot-title">四种模式</div><div class="mv-foot-grid">')
    for cls, k, v in MODE_CARDS:
        html.append(f'<div class="mv-foot-card {cls}"><div class="mv-fk">{k}</div><div class="mv-fv">{v}</div></div>')
    html.append("</div></div>")

    html.append('<div class="mv-foot-sec"><div class="mv-foot-title">引擎信息</div><div class="mv-foot-grid">')
    for cls, k, v in engine:
        html.append(f'<div class="mv-foot-card {cls}"><div class="mv-fk">{k}</div><div class="mv-fv">{v}</div></div>')
    html.append("</div></div>")

    html.append("</div>")
    return "\n".join(html)


# ---------------------------------------------------------------- 数据探测（只读）
def probe_case_count(db_path: str = "cases/cases.db") -> int:
    """读用例条数用于信息展示；失败返回 0，不影响主流程。"""
    import sqlite3
    path = db_path if os.path.isabs(db_path) else str(_HERE / db_path)
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM cases")
        n = cur.fetchone()[0]
        conn.close()
        return int(n)
    except Exception:
        return 0
