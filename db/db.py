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
