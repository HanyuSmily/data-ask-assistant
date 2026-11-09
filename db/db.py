# -*- coding: utf-8 -*-
"""SQLite 成长档案：连接管理 + 初始化 + 写入封装"""
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent.db")
SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def init_db():
    """确保所有表都已存在（幂等，多次调用安全）"""
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        schema_sql = f.read()
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(schema_sql)
    conn.commit()
    conn.close()


@contextmanager
def get_conn():
    """使用 with get_conn() as conn: 的上下文管理器"""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ---------- 写入封装 ----------

def create_session(initial_mode: str, source: str = "web",
                   user_agent: str = "", os_info: str = "") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO sessions (started_at, initial_mode, source, user_agent, os_info) "
            "VALUES (?,?,?,?,?)",
            (_now_iso(), initial_mode, source, user_agent, os_info),
        )
        return cur.lastrowid


def end_session(session_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE sessions SET ended_at=? WHERE id=?",
            (_now_iso(), session_id),
        )


def insert_user_message(session_id: int, mode_at_send: str, content: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO messages (session_id, created_at, role, mode_at_send, content) "
            "VALUES (?,?,?,?,?)",
            (session_id, _now_iso(), "user", mode_at_send, content),
        )
        return cur.lastrowid


def insert_assistant_message(
    session_id: int,
    mode_at_send: str,
    content: str,
    prompt_name: str = "",
    model_used: str = "",
    tokens_system: int = 0,
    tokens_input: int = 0,
    tokens_output: int = 0,
    tokens_total: int = 0,
    latency_ms: int = 0,
    case_ids_referenced: list[str] | None = None,
    reply_type: str = "",
    is_success: int = 1,
) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO messages (
                session_id, created_at, role, mode_at_send, content,
                prompt_name, model_used, tokens_system, tokens_input,
                tokens_output, tokens_total, latency_ms,
                case_ids_referenced, reply_type, is_success
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                session_id, _now_iso(), "assistant", mode_at_send, content,
                prompt_name, model_used, tokens_system, tokens_input,
                tokens_output, tokens_total, latency_ms,
                json.dumps(case_ids_referenced or [], ensure_ascii=False),
                reply_type, is_success,
            ),
        )
        return cur.lastrowid


# ---------- 查询（周统计） ----------

def week_stats(week_start: str | None = None):
    """读 week_stats 视图；week_start 形如 '2026-11-02'，None 取最新一周"""
    with get_conn() as conn:
        if week_start:
            rows = conn.execute(
                "SELECT * FROM week_stats WHERE week_start = ?", (week_start,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM week_stats ORDER BY week_start DESC"
            ).fetchall()
        return [dict(r) for r in rows]


# ---------- saved_sessions：会话收藏 ----------

def save_session(mode: str, messages: list[dict]) -> int | None:
    """把一段对话存下来。messages 是 Chatbot history 列表，每项 {role, content}。"""
    # 过滤成纯 dict 格式（Chatbot 可能是 tuple 或 dict）
    clean: list[dict] = []
    for m in messages or []:
        if isinstance(m, dict):
            clean.append({"role": m.get("role", ""), "content": m.get("content", "")})
        elif isinstance(m, (list, tuple)) and len(m) == 2:
            clean.append({"role": m[0], "content": m[1]})

    if not clean:
        return None

    # title = 第一条用户消息的前 20 字
    title = "（空对话）"
    for m in clean:
        if m.get("role") == "user":
            title = (m.get("content") or "").strip()[:20] or "（空对话）"
            break

    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO saved_sessions (mode, title, messages, msg_count, created_at)
               VALUES (?,?,?,?,?)""",
            (mode, title, json.dumps(clean, ensure_ascii=False), len(clean), _now_iso()),
        )
        return cur.lastrowid


def list_saved_sessions(mode_filter: str | None = None) -> list[dict]:
    """列出已保存的会话（不含 messages 全文，侧边栏够用）。"""
    sql = "SELECT id, mode, title, msg_count, created_at FROM saved_sessions"
    params: list = []
    if mode_filter and mode_filter != "全部":
        sql += " WHERE mode = ?"
        params.append(mode_filter)
    sql += " ORDER BY created_at DESC"
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def get_saved_session(saved_id: int) -> dict | None:
    """拉一条完整的收藏（含 messages JSON）。"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM saved_sessions WHERE id = ?", (saved_id,),
        ).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["messages"] = json.loads(d["messages"])
        return d


def delete_saved_session(saved_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM saved_sessions WHERE id = ?", (saved_id,))
