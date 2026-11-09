-- 测试陪练 Agent 成长档案 schema
-- 运行：python db/db.py（自动初始化 sqlite 文件）

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- sessions: 一轮对话（UI 初始化时创建，跨多条 message）
CREATE TABLE IF NOT EXISTS sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    initial_mode    TEXT NOT NULL,
    source          TEXT DEFAULT 'web',
    user_agent      TEXT,
    os_info         TEXT
);

-- messages: 每条往返对话
CREATE TABLE IF NOT EXISTS messages (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    created_at          TEXT NOT NULL,
    role                TEXT NOT NULL CHECK (role IN ('user','assistant')),
    mode_at_send        TEXT NOT NULL,
    content             TEXT NOT NULL,
    prompt_name         TEXT,
    prompt_version      TEXT DEFAULT 'v1',
    model_used          TEXT,
    tokens_system       INTEGER DEFAULT 0,
    tokens_input        INTEGER DEFAULT 0,
    tokens_output       INTEGER DEFAULT 0,
    tokens_total        INTEGER DEFAULT 0,
    latency_ms          INTEGER DEFAULT 0,
    case_ids_referenced TEXT,
    reply_type          TEXT,
    is_success          INTEGER DEFAULT 1,
    user_reaction       TEXT CHECK (user_reaction IN ('thumbs_up','thumbs_down'))
);

CREATE INDEX IF NOT EXISTS idx_msg_sess_created ON messages(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_msg_mode_created  ON messages(mode_at_send, created_at);
CREATE INDEX IF NOT EXISTS idx_msg_role          ON messages(role);
CREATE INDEX IF NOT EXISTS idx_msg_is_success    ON messages(is_success);

-- quiz_answers: 出题模式专属
CREATE TABLE IF NOT EXISTS quiz_answers (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id          INTEGER NOT NULL UNIQUE REFERENCES messages(id) ON DELETE CASCADE,
    case_candidate      TEXT,
    score_coverage      INTEGER,
    score_depth         INTEGER,
    score_boundary      INTEGER,
    score_executability INTEGER,
    score_originality   INTEGER,
    total_score         INTEGER,
    gap_summary         TEXT
);

-- week_stats VIEW
CREATE VIEW IF NOT EXISTS week_stats AS
SELECT
    date(m.created_at, 'weekday 0', '-6 days')                         AS week_start,
    date(m.created_at, 'weekday 0')                                    AS week_end,
    m.mode_at_send                                                      AS mode,
    count(*)                                                            AS msg_count,

    sum(CASE WHEN m.case_ids_referenced IS NOT NULL
              AND m.case_ids_referenced != '[]' THEN 1 ELSE 0 END)
                                                                       AS with_case_ref,
    round(
      100.0 * sum(CASE WHEN m.case_ids_referenced IS NOT NULL
                       AND m.case_ids_referenced != '[]' THEN 1 ELSE 0 END)
      / nullif(count(*), 0), 2
    )                                                                   AS case_ref_rate_pct,

    sum(CASE WHEN m.mode_at_send = '陪练模式'
              AND m.reply_type = '引导反问' THEN 1 ELSE 0 END)
                                                                       AS practice_probe_count,

    round(
      100.0 * sum(CASE WHEN m.mode_at_send = '速查模式'
                        THEN 1 ELSE 0 END)
      / nullif(count(*), 0), 2
    )                                                                   AS quickref_ratio_pct,

    avg(m.latency_ms)                                                  AS avg_latency_ms,
    sum(m.tokens_total)                                                AS sum_tokens,
    avg(m.tokens_total)                                                AS avg_tokens

FROM messages m
WHERE m.role = 'assistant'
GROUP BY week_start, mode
ORDER BY week_start DESC;
