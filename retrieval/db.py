# -*- coding: utf-8 -*-
"""SQLite 建库 + FTS5 索引 + 示例数据 + CSV 增量导入。"""
import csv
import json
import os
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "cases" / "cases.db"


# ============ 建表 SQL ============

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    case_id        INTEGER PRIMARY KEY,
    case_name      TEXT NOT NULL,
    catalog_lv1    TEXT,           -- 一级分类（冗余加速）
    catalog_lv2    TEXT,
    catalog_lv3    TEXT,
    catalog_path   TEXT,           -- 完整路径，分隔符已归一化
    priority       TEXT,           -- P0 / P1 / P2
    precondition   TEXT,
    steps          TEXT,
    expected       TEXT,
    tags           TEXT,           -- JSON array 或逗号分隔
    last_result    TEXT DEFAULT NULL,
    owner          TEXT DEFAULT NULL,
    updated_at     TEXT DEFAULT CURRENT_TIMESTAMP
);

-- FTS5 全文索引：存 jieba 分词后的字段，用空格连接
-- 中文分词结果写进去后，FTS5 的默认 ascii 分词器对空格分隔刚好够用
CREATE VIRTUAL TABLE IF NOT EXISTS cases_fts USING fts5(
    case_name_terms,
    all_terms,
    tokenize='unicode61'
);

-- 同义词表
CREATE TABLE IF NOT EXISTS term_aliases (
    term    TEXT PRIMARY KEY,
    aliases TEXT NOT NULL          -- JSON array: ["别名1", "别名2"]
);

-- 模块目录索引（从 catalog_lv1 抽取，给系统提示词常驻）
CREATE TABLE IF NOT EXISTS module_index (
    module_name TEXT PRIMARY KEY,
    lv1_count   INTEGER DEFAULT 0,
    case_count  INTEGER DEFAULT 0
);
"""


def get_conn(db_path: str | os.PathLike = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """获取连接 + 启用 WAL 模式"""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str | os.PathLike = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """建表 + 建 FTS + 同义词。幂等。"""
    conn = get_conn(db_path)
    conn.executescript(SCHEMA)
    _ensure_default_aliases(conn)
    # FTS5 独立表模式：INSERT 时用 rowid 关联 cases.case_id，
    # rebuild_fts() 手动重建，不走 trigger
    conn.commit()
    return conn


# def _ensure_fts_triggers 已废弃（FTS5 独立表模式）

DEFAULT_ALIASES: dict[str, list[str]] = {
    # --- IPC 故障/状态 ---
    "brick":      ["变砖", "无限重启", "开不了机"],
    "bootloop":   ["无限重启", "反复重启", "重启循环"],
    "crash":      ["崩溃", "闪退", "异常退出"],
    "hang":       ["卡住", "无响应", "超时", "卡死"],
    "lag":        ["卡顿", "延迟", "延时", "不流畅"],
    "nightvision": ["夜视", "红外", "黑白画面", "日夜切换"],
    "ota":        ["升级", "固件更新", "固件升级", "firmware"],
    "recovery":   ["回滚", "恢复出厂", "恢复默认"],
    "powercycle": ["断电", "重启", "上电", "开机"],
    # --- 网络 ---
    "wifi":       ["无线", "无线网络", "wlan"],
    "network":    ["网络", "联网", "连接"],
    "signal":     ["信号", "接收", "强度"],
    "dns":        ["域名解析", "解析失败"],
    # --- 视频/编码 ---
    "bitrate":    ["码率", "带宽"],
    "resolution": ["分辨率", "清晰度"],
    "codec":      ["编码", "h264", "h265", "hevc", "avc"],
    "fps":        ["帧率", "画面掉帧"],
    "motion":     ["移动侦测", "人形检测", "运动检测", "报警"],
    "ptz":        ["云台", "pan tilt zoom"],
    # --- 存储 ---
    "sdcard":     ["sd卡", "tf卡", "内存卡", "存储卡"],
    "cloud":      ["云存储", "云端", "cloud storage"],
    "record":     ["录像", "录制", "抓拍", "截图"],
    # --- 系统 ---
    "timezone":   ["时区", "时间同步", "osd时间"],
    "ntp":        ["时间同步", "校时"],
    "account":    ["账号", "登录", "配对"],
    "privacy":    ["隐私区域", "遮挡区域"],
}


def _ensure_default_aliases(conn: sqlite3.Connection):
    conn.execute("DELETE FROM term_aliases")
    for term, aliases in DEFAULT_ALIASES.items():
        conn.execute(
            "INSERT OR REPLACE INTO term_aliases(term, aliases) VALUES (?, ?)",
            (term, json.dumps(aliases, ensure_ascii=False)),
        )


def load_aliases(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """从 DB 读同义词表，返回 {term: [aliases]}。"""
    rows = conn.execute("SELECT term, aliases FROM term_aliases").fetchall()
    return {r["term"]: json.loads(r["aliases"]) for r in rows}


# ============ 目录归一化 ============

def normalize_catalog(raw: str) -> tuple[str, str, str, str]:
    """把 '所有用例｜基线用例｜IPC嵌入式｜长电用例｜通用功能｜系统功能｜OTA升级'
    归一化 → '/所有用例/基线用例/IPC嵌入式/长电用例/通用功能/系统功能/OTA升级'
    并拆出 lv1 / lv2 / lv3。"""
    if not raw:
        return "", "", "", ""
    # 统一分隔符
    parts = raw.replace("｜", "/").replace("|", "/").replace("\\", "/").split("/")
    parts = [p.strip() for p in parts if p.strip()]
    full = "/" + "/".join(parts)
    lv1 = parts[4] if len(parts) > 4 else (parts[0] if parts else "")  # 跳过前缀
    lv2 = parts[5] if len(parts) > 5 else ""
    lv3 = parts[6] if len(parts) > 6 else ""
    return full, lv1, lv2, lv3


# ============ 示例数据（25 条，覆盖 9 个模块） ============

SAMPLE_CASES = [
    # --- OTA 升级 ---
    (1001, "OTA升级过程中断电恢复", "通用功能", "系统功能", "OTA升级",
     "升级到 50% 时断电，重新上电观察设备行为",
     "1. 下载最新固件到设备 2. 点击 OTA 升级 3. 等待进度到 50% 突然断电 4. 重新上电",
     "设备应自动回滚到旧版本，不进入无限重启或变砖状态", "P0", "ota,断电,回滚", "PASS", "张三"),
    (1002, "OTA升级版本跨大版本", "通用功能", "系统功能", "OTA升级",
     "从 v1.0 跨到 v3.0", "1. 当前版本 v1.0 2. 下载 v3.0 固件 3. 执行升级",
     "升级成功，所有历史配置保留，无回滚", "P0", "ota,跨版本", "PASS", "张三"),
    (1003, "OTA升级包大小边界", "通用功能", "系统功能", "OTA升级",
     "测试 200MB 以上固件包升级", "1. 准备 250MB 固件包 2. 上传到服务器 3. 设备拉取升级",
     "大包下载不超时，升级成功率 ≥ 95%", "P1", "ota,固件包", "PASS", "张三"),
    (1004, "OTA升级网络中断恢复", "通用功能", "系统功能", "OTA升级",
     "升级下载到 80% 时断开 Wi-Fi 10 秒", "1. 启动 OTA 下载 2. 进度 80% 时断网 3. 10 秒后恢复",
     "下载断点续传，不从头开始", "P1", "ota,网络", "FAIL", "张三"),

    # --- 时区同步 ---
    (2001, "设备时区随手机同步", "通用功能", "系统功能", "时区修改",
     "手机时区改为新西兰/奥克兰", "1. 手机时区改 UTC+12 2. 配网 3. 看预览画面 OSD 时间",
     "OSD 时间与手机一致", "P1", "时区,osd", "PASS", "李四"),
    (2002, "时区切换时录像时间戳", "通用功能", "系统功能", "时区修改",
     "录像文件时间戳随时区变化", "1. 录制 1 分钟视频 2. 切时区 3. 再录 1 分钟 4. 对比时间戳",
     "两个录像文件时间戳分别对应各自时区", "P1", "时区,录像", "PASS", "李四"),

    # --- 码率分辨率 ---
    (3001, "主码率 1080p 码率范围", "通用功能", "音视频能力", "视频编码",
     "主码流高清模式码率应在 2~4Mbps", "1. 设为主码率 1080p 标准模式 2. 录制 2min 3. MediaInfo 分析",
     "码率 2048kbps ~ 4096kbps", "P0", "码率,分辨率,1080p", "PASS", "王五"),
    (3002, "子码率流畅模式码率", "通用功能", "音视频能力", "视频编码",
     "子码率 720p 流畅模式码率 0.5~0.7Mbps", "1. 子码率 720p 流畅模式 2. 录制 2min 3. MediaInfo",
     "码率 512kbps ~ 716kbps", "P0", "码率,子码率,流畅", "PASS", "王五"),
    (3003, "H264 vs H265 编码格式", "通用功能", "音视频能力", "视频编码",
     "主码流 H265，子码流 H264", "1. 设置编码格式 2. 录制 3. MediaInfo 查看编码",
     "主码流 HEVC(H265)，子码流 AVC(H264)", "P1", "编码,h264,h265", "PASS", "王五"),

    # --- 云存储 ---
    (4001, "云存储事件自动触发", "通用功能", "音视频能力", "事件云存储",
     "移动侦测触发后 3 秒内上传云端", "1. 开启云存储 + 移动侦测 2. 制造动静 3. 看云端",
     "3 秒内有事件录像上传", "P0", "云存储,事件,motion", "PASS", "赵六"),
    (4002, "云存储存储空间不足提示", "通用功能", "音视频能力", "事件云存储",
     "云存满 95% 时 App 推送", "1. 写入云存储到 95% 2. 再触发一次事件录像",
     "App 收到存储告警推送", "P1", "云存储,告警", "BLOCK", "赵六"),
    (4003, "云存储录像下载速度", "通用功能", "音视频能力", "事件云存储",
     "1080p 1min 录像 ≤ 10s 下载", "1. 录一段云存视频 2. App 下载 3. 计时",
     "下载 ≤ 10s（良好网络下）", "P2", "云存储,下载", "PASS", "赵六"),

    # --- 夜视 ---
    (5001, "夜视模式红外灯自动开启", "通用功能", "基本设置", "日夜切换",
     "亮度 < 10lux 自动切夜视 + 开红外", "1. 进入暗房 2. 测亮度 3. 看画面和红外灯",
     "10lux 以下红外灯亮，画面变黑白", "P0", "夜视,红外,lux", "PASS", "孙七"),
    (5002, "夜视画面噪点控制", "通用功能", "基本设置", "日夜切换",
     "夜视模式低照度噪点不明显", "1. 暗房 5lux 环境 2. 录像 3. 对比同价位机型",
     "信噪比 ≥ 35dB", "P1", "夜视,噪点", "PASS", "孙七"),

    # --- 移动侦测 ---
    (6001, "人形过滤不误报树木", "通用功能", "侦测功能", "人形过滤",
     "人形过滤开启后风吹树叶不触发报警", "1. 开启人形过滤 2. 对准树木 3. 等 5 分钟看报警",
     "树木晃动不触发，人走过触发", "P0", "侦测,人形,误报", "PASS", "周八"),
    (6002, "移动侦测灵敏度 1~5 档", "通用功能", "侦测功能", "单区域侦测",
     "5 档最灵敏、1 档最钝", "1. 分别设灵敏度 1/3/5 2. 同样动作走过 3. 对比触发率",
     "灵敏度 5 触发率 > 灵敏度 3 > 灵敏度 1", "P1", "侦测,灵敏度", "PASS", "周八"),
    (6003, "移动侦测区域自定义", "通用功能", "侦测功能", "单区域侦测",
     "可框选 3~4 个侦测区域", "1. App 进入侦测设置 2. 框选区域 A/B/C 3. 分别触发",
     "各区域独立生效，互不干扰", "P1", "侦测,区域", "FAIL", "周八"),

    # --- 本地录像 ---
    (7001, "本地录像 SD 卡循环覆盖", "通用功能", "系统功能", "存储设置",
     "SD 卡满了最旧录像被覆盖", "1. 录制到 SD 卡满 2. 再录 10 分钟 3. 看最早录像",
     "最早录像被新内容覆盖，无丢失空间错误", "P0", "sd卡,覆盖,循环", "PASS", "吴九"),
    (7002, "本地录像 SD 卡损坏提示", "通用功能", "系统功能", "存储设置",
     "拔出 SD 卡后 App 立即提示", "1. 正常录像中拔 SD 卡 2. 看 App 和设备",
     "App 收到 SD 卡异常推送，设备红灯闪烁", "P1", "sd卡,损坏,提示", "PASS", "吴九"),

    # --- 云台 ---
    (8001, "云台预设点精度", "云台机功能", "云台功能", "预设点",
     "云台回到预设点偏差 ≤ 2°", "1. 设预设点 A 2. 云台转到别处 3. 一键回 A 4. 量角度",
     "偏差 ≤ 2°", "P0", "云台,预设点,精度", "PASS", "郑十"),
    (8002, "云台 360° 连续转动", "云台机功能", "云台功能", "云台校准",
     "360° 连续转不卡顿", "1. App 触发 360° 旋转 2. 全程录像 3. 看画面是否有卡顿",
     "画面流畅，无明显卡顿或丢帧", "P1", "云台,卡顿", "PASS", "郑十"),

    # --- 产测 ---
    (9001, "产测老化标志位读取", "产测用例", "", "",
     "产测跑完后上位机能读到老化标志", "1. 烧录产测固件 2. 执行老化测试 3. 上位机读标志",
     "老化标志 = 1", "P0", "产测,老化", "PASS", "钱十一"),
    (9002, "产测失败重测", "产测用例", "", "",
     "产测某一步失败，可重测", "1. 故意断网让产测失败 2. 恢复网络 3. 重新触发产测",
     "产测从失败步骤继续，不从头开始", "P1", "产测,重测", "PASS", "钱十一"),

    # --- AI ---
    (10001, "AI 角色切换双击", "带屏IPC", "AI二期需求", "",
     "双击按键切换 AI 角色", "1. 默认角色启动 2. 双击按键 3. 观察角色变化",
     "角色按顺序循环切换，切换动画正常", "P0", "ai,角色,切换", "FAIL", "冯十二"),
    (10002, "AI 预设角色列表", "带屏IPC", "AI二期需求", "",
     "后台可配置最多 5 个角色", "1. 后台配置 5 个角色 2. 设备端双击切换",
     "5 个角色都能切换到", "P1", "ai,角色", "PASS", "冯十二"),
]


def seed_samples(conn: sqlite3.Connection):
    """写入示例数据（幂等：已存在 case_id 跳过）。"""
    cursor = conn.execute("SELECT MAX(case_id) FROM cases")
    existing_max = cursor.fetchone()[0] or 0
    rows_inserted = 0
    for row in SAMPLE_CASES:
        case_id = row[0]
        if case_id <= existing_max:
            # 检查是否已存在
            exists = conn.execute("SELECT 1 FROM cases WHERE case_id=?", (case_id,)).fetchone()
            if exists:
                continue
        conn.execute(
            """INSERT OR IGNORE INTO cases
               (case_id, case_name, catalog_lv1, catalog_lv2, catalog_lv3,
                precondition, steps, expected, priority, tags, last_result, owner)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            row,
        )
        rows_inserted += 1

    # 补 catalog_path（根据 lv1/lv2/lv3 重建）
    conn.execute("""
        UPDATE cases SET catalog_path = COALESCE(catalog_lv1,'')
          || CASE WHEN catalog_lv2 THEN '/' || catalog_lv2 ELSE '' END
          || CASE WHEN catalog_lv3 THEN '/' || catalog_lv3 ELSE '' END
        WHERE catalog_path IS NULL OR catalog_path = ''
    """)
    conn.commit()
    return rows_inserted


def rebuild_fts(conn: sqlite3.Connection):
    """全量重建 FTS5 索引。用 jieba 分词填充。"""
    import jieba
    conn.execute("DELETE FROM cases_fts")
    rows = conn.execute(
        "SELECT case_id, case_name, precondition, steps, expected, tags, catalog_path FROM cases"
    ).fetchall()
    def j(text):
        return " ".join(w.strip() for w in jieba.cut(text or "") if w.strip())
    data = []
    for r in rows:
        data.append((
            r[0],
            j(r[1]),  # case_name_terms
            j(f"{r[1] or ''} {r[2] or ''} {r[3] or ''} {r[4] or ''} {r[5] or ''} {r[6] or ''}")
        ))
    conn.executemany("INSERT INTO cases_fts(rowid, case_name_terms, all_terms) VALUES (?,?,?)", data)
    conn.commit()


def update_module_index(conn: sqlite3.Connection):
    """从 cases 表聚合 catalog_lv1，写 module_index。"""
    conn.execute("DELETE FROM module_index")
    conn.execute("""
        INSERT INTO module_index(module_name, lv1_count, case_count)
        SELECT catalog_lv1, COUNT(*), COUNT(*) FROM cases
        WHERE catalog_lv1 IS NOT NULL AND catalog_lv1 != ''
        GROUP BY catalog_lv1
    """)
    conn.commit()


# ============ CSV 导入（UTF-8-BOM，增量） ============

CSV_COL_MAP = {
    # CSV 列名 → cases 表列名
    "case_id":       "case_id",
    "title":         "case_name",
    "case_name":     "case_name",
    "priority":      "priority",
    "tags":          "tags",
    "owner":         "owner",
    "last_result":   "last_result",
    "precondition":  "precondition",
    "preconditions": "precondition",
    "steps":         "steps",
    "step":          "steps",
    "expected":      "expected",
    "catalog_path":  "catalog_path",
    "category":      "catalog_path",
}


def import_csv(conn: sqlite3.Connection, csv_path: str | os.PathLike,
               dry_run: bool = False) -> dict:
    """导入真实 CSV。支持增量（按 case_id UPSERT）。
    CSV 首行必须是列名，UTF-8 或 UTF-8-BOM。"""
    result = {"read": 0, "inserted": 0, "updated": 0, "skipped": 0, "errors": []}
    csv_path = Path(csv_path)
    if not csv_path.exists():
        result["errors"].append(f"文件不存在: {csv_path}")
        return result

    # 尝试 UTF-8-BOM → UTF-8
    raw = csv_path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        text = raw[3:].decode("utf-8")
    else:
        text = raw.decode("utf-8", errors="replace")

    reader = csv.DictReader(text.splitlines())
    # 把 CSV 列名 normalize 成小写
    fieldnames = [f.strip().lower() for f in (reader.fieldnames or [])]

    for row in reader:
        result["read"] += 1
        try:
            # 收集字段
            data = {}
            for csv_col, db_col in CSV_COL_MAP.items():
                if csv_col in row and row[csv_col]:
                    data[db_col] = row[csv_col].strip()

            if "case_id" not in data or not data["case_id"]:
                result["skipped"] += 1
                continue

            # catalog_path 归一化 → 拆 lv1/lv2/lv3
            if "catalog_path" in data:
                full, lv1, lv2, lv3 = normalize_catalog(data["catalog_path"])
                data["catalog_path"] = full
                data["catalog_lv1"] = lv1
                data["catalog_lv2"] = lv2
                data["catalog_lv3"] = lv3

            if dry_run:
                continue

            # UPSERT
            placeholders = ",".join("?" * len(data))
            col_names = ",".join(data.keys())
            updates = ",".join(f"{k}=excluded.{k}" for k in data.keys() if k != "case_id")
            sql = (f"INSERT INTO cases({col_names}) VALUES({placeholders}) "
                   f"ON CONFLICT(case_id) DO UPDATE SET {updates}, updated_at=CURRENT_TIMESTAMP")
            before = conn.execute("SELECT 1 FROM cases WHERE case_id=?",
                                  (int(data["case_id"]),)).fetchone()
            conn.execute(sql, list(data.values()))
            if before:
                result["updated"] += 1
            else:
                result["inserted"] += 1

        except Exception as e:
            result["errors"].append(f"行 {result['read']}: {e}")
            result["skipped"] += 1

    conn.commit()
    if not dry_run:
        rebuild_fts(conn)
        update_module_index(conn)
    return result


# ============ 模块目录索引（系统提示词用） ============

def get_module_index(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("""
        SELECT catalog_lv1, COUNT(*) AS cnt FROM cases
        WHERE catalog_lv1 IS NOT NULL AND catalog_lv1 != ''
        GROUP BY catalog_lv1
        ORDER BY cnt DESC
    """).fetchall()
    return [{"module": r["catalog_lv1"], "count": r["cnt"]} for r in rows]


def format_module_index_for_prompt(conn: sqlite3.Connection) -> str:
    """输出给系统提示词常驻的模块索引文本。"""
    rows = get_module_index(conn)
    if not rows:
        return ""
    lines = ["## 用例模块索引", "以下是已收录的测试模块（一级分类）：", ""]
    for r in rows:
        lines.append(f"- {r['module']}（{r['count']} 条用例）")
    return "\n".join(lines)
