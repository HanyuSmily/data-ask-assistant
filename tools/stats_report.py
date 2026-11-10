#!/usr/bin/env python3
"""埋点统计报表生成器

从 db/agent.db 的 messages 表读取对话埋点，生成一份可直接截图贴进报告/PPT 的 HTML 报表。

用法：
    python tools/stats_report.py                    # 生成到 output/stats-report.html
    python tools/stats_report.py --out xx.html      # 指定输出路径
    python tools/stats_report.py --stdout           # 只在终端打印表格

统计口径：
    - 只统计 role='assistant' 的记录（每条 = 一轮完整回复）
    - 默认排除 is_success=0 的失败记录（LLM 超时等），避免污染延迟均值
"""

from __future__ import annotations

import argparse
import html
import os
import sqlite3
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "db", "agent.db")
DEFAULT_OUT = os.path.join(ROOT, "output", "stats-report.html")

MODE_ORDER = ["陪练模式", "速查模式", "出题模式", "执行模式"]


def _q(conn: sqlite3.Connection, sql: str) -> list[sqlite3.Row]:
    return list(conn.execute(sql))


def collect(db_path: str, include_failed: bool = False) -> dict:
    """读取并汇总埋点数据"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cond = "role='assistant'" if include_failed else "role='assistant' AND is_success=1"

    by_mode = _q(conn, f"""
        SELECT mode_at_send                                        AS mode,
               COUNT(*)                                            AS rounds,
               ROUND(AVG(latency_ms))                              AS avg_latency,
               MIN(latency_ms)                                     AS min_latency,
               MAX(latency_ms)                                     AS max_latency,
               ROUND(AVG(tokens_input))                            AS avg_tokens_in,
               ROUND(AVG(tokens_output))                           AS avg_tokens_out,
               ROUND(AVG(tokens_total))                            AS avg_tokens_total,
               SUM(tokens_total)                                   AS sum_tokens,
               SUM(CASE WHEN reply_type='引导反问' THEN 1 ELSE 0 END) AS probe_count
        FROM messages
        WHERE {cond}
        GROUP BY mode_at_send
        ORDER BY rounds DESC
    """)

    by_type = _q(conn, f"""
        SELECT reply_type AS rtype, COUNT(*) AS n
        FROM messages
        WHERE {cond} AND reply_type IS NOT NULL AND reply_type != ''
        GROUP BY reply_type
        ORDER BY n DESC
    """)

    detail = _q(conn, f"""
        SELECT id,
               replace(substr(created_at, 6, 11), 'T', ' ')       AS ts,
               mode_at_send                                        AS mode,
               reply_type                                          AS rtype,
               tokens_total                                        AS tok,
               latency_ms                                          AS lat
        FROM messages
        WHERE {cond}
        ORDER BY id DESC
        LIMIT 12
    """)

    span = conn.execute("SELECT MIN(created_at) a, MAX(created_at) b FROM messages").fetchone()
    total_rounds = conn.execute(f"SELECT COUNT(*) FROM messages WHERE {cond}").fetchone()[0]
    failed = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE role='assistant' AND is_success=0"
    ).fetchone()[0]
    conn.close()

    return {
        "by_mode": by_mode,
        "by_type": by_type,
        "detail": detail,
        "span": (span["a"], span["b"]),
        "total_rounds": total_rounds,
        "failed": failed,
    }


def print_console(d: dict, include_failed: bool) -> None:
    """终端表格输出（可直接截图终端窗口）"""
    print("=" * 74)
    print("  对话埋点统计   数据源: db/agent.db → messages")
    print(f"  统计区间: {d['span'][0]} ~ {d['span'][1]}")
    print(f"  口径: assistant 回复 / {'含失败' if include_failed else '仅成功'} / 共 {d['total_rounds']} 轮")
    print("=" * 74)
    print()
    print(f"{'模式':<10}{'轮数':>6}{'平均延迟':>11}{'平均token':>10}{'引导反问':>9}{'反问率':>9}")
    print("-" * 74)
    for r in d["by_mode"]:
        rate = f"{r['probe_count'] / r['rounds'] * 100:.0f}%" if r["rounds"] else "-"
        lat = f"{r['avg_latency'] / 1000:.1f}s" if r["avg_latency"] else "-"
        tok = f"{r['avg_tokens_total']:.0f}" if r["avg_tokens_total"] else "-"
        print(f"{r['mode']:<10}{r['rounds']:>6}{lat:>11}{tok:>10}{r['probe_count']:>9}{rate:>9}")
    print("-" * 74)
    print()
    print("回复类型分布:")
    for r in d["by_type"]:
        print(f"  {r['rtype']:<11}{r['n']:>3} 次")
    if d["failed"]:
        print(f"\n注: 另有 {d['failed']} 条失败记录(LLM 超时)未计入统计")
    print()


def build_html(d: dict, include_failed: bool) -> str:
    """生成浅色报表 HTML，适合直接截图"""
    def esc(x) -> str:
        return html.escape(str(x))

    probe_total = sum(r["probe_count"] for r in d["by_mode"])
    practice = next((r for r in d["by_mode"] if r["mode"] == "陪练模式"), None)
    probe_rate = (practice["probe_count"] / practice["rounds"] * 100) if practice and practice["rounds"] else 0
    avg_tok = sum(r["avg_tokens_total"] * r["rounds"] for r in d["by_mode"]) / d["total_rounds"] if d["total_rounds"] else 0

    mode_rows = []
    for r in d["by_mode"]:
        rate = f"{r['probe_count'] / r['rounds'] * 100:.0f}%" if r["rounds"] else "—"
        rate_cls = "hl" if r["probe_count"] and r["probe_count"] == r["rounds"] else ""
        mode_rows.append(f"""
        <tr>
          <td class="l">{esc(r['mode'])}</td>
          <td>{r['rounds']}</td>
          <td>{r['avg_latency'] / 1000:.1f} s</td>
          <td>{r['min_latency'] / 1000:.1f} / {r['max_latency'] / 1000:.1f} s</td>
          <td>{r['avg_tokens_in']:.0f}</td>
          <td>{r['avg_tokens_out']:.0f}</td>
          <td>{r['avg_tokens_total']:.0f}</td>
          <td class="{rate_cls}">{rate}</td>
        </tr>""")

    type_rows = "".join(
        f'<tr><td class="l">{esc(r["rtype"])}</td><td>{r["n"]}</td>'
        f'<td>{r["n"] / d["total_rounds"] * 100:.1f}%</td></tr>'
        for r in d["by_type"]
    )

    detail_rows = "".join(
        f'<tr><td class="mono">{esc(r["ts"])}</td><td class="l">{esc(r["mode"])}</td>'
        f'<td class="l">{esc(r["rtype"] or "—")}</td><td>{r["tok"] or "—"}</td>'
        f'<td>{r["lat"] / 1000:.1f} s</td></tr>'
        for r in d["detail"]
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>对话埋点统计报表</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 36px 40px 44px;
    width: 1000px;
    background: #ffffff;
    color: #1a2332;
    font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
    font-size: 13px;
    -webkit-font-smoothing: antialiased;
  }}
  h1 {{ font-size: 21px; margin: 0 0 6px; letter-spacing: .5px; color: #0f172a; }}
  .sub {{ color: #64748b; font-size: 12px; margin-bottom: 22px; line-height: 1.7; }}
  .sub b {{ color: #334155; font-weight: 600; }}
  .cards {{ display: flex; gap: 12px; margin-bottom: 26px; }}
  .card {{
    flex: 1; padding: 14px 16px; border-radius: 8px;
    background: #f6f9fc; border: 1px solid #e2e8f0; border-left: 3px solid #3b82f6;
  }}
  .card .k {{ font-size: 11px; color: #64748b; margin-bottom: 6px; }}
  .card .v {{ font-size: 22px; font-weight: 700; color: #1d4ed8; font-variant-numeric: tabular-nums; }}
  .card .v small {{ font-size: 12px; font-weight: 500; color: #64748b; }}
  h2 {{
    font-size: 13px; margin: 26px 0 10px; padding-left: 9px;
    border-left: 3px solid #1d4ed8; color: #0f172a; font-weight: 700;
  }}
  h2 span {{ font-weight: 400; color: #94a3b8; font-size: 11px; margin-left: 8px; }}
  table {{ width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }}
  th {{
    background: #eff6ff; color: #1e40af; font-weight: 600; font-size: 12px;
    padding: 9px 10px; text-align: center; border-bottom: 2px solid #bfdbfe;
    white-space: nowrap;
  }}
  td {{ padding: 8px 10px; text-align: center; border-bottom: 1px solid #eef2f7; color: #334155; }}
  td.l {{ text-align: left; }}
  td.mono {{ font-family: Consolas, monospace; font-size: 12px; color: #475569; }}
  tbody tr:nth-child(even) {{ background: #fafcfe; }}
  td.hl {{ color: #dc2626; font-weight: 700; }}
  .note {{ margin-top: 22px; padding: 11px 14px; background: #fffbeb; border: 1px solid #fde68a;
           border-radius: 6px; color: #92400e; font-size: 11.5px; line-height: 1.75; }}
  .foot {{ margin-top: 18px; color: #94a3b8; font-size: 11px; text-align: right; }}
</style>
</head>
<body>

<h1>对话埋点统计报表</h1>
<div class="sub">
  数据源：<b>SQLite → db/agent.db → messages 表</b>　|　
  统计区间：<b>{esc(d['span'][0][:10])} ~ {esc(d['span'][1][:10])}</b>　|　
  口径：assistant 回复，共 <b>{d['total_rounds']}</b> 轮{'' if include_failed else '（已排除失败请求）'}
</div>

<div class="cards">
  <div class="card"><div class="k">累计对话轮次</div><div class="v">{d['total_rounds']} <small>轮</small></div></div>
  <div class="card"><div class="k">平均单轮 token</div><div class="v">{avg_tok:.0f} <small>tokens</small></div></div>
  <div class="card"><div class="k">陪练模式引导反问率</div><div class="v">{probe_rate:.0f}<small>% ({probe_total}/{practice['rounds'] if practice else 0})</small></div></div>
  <div class="card"><div class="k">平均响应延迟</div><div class="v">{sum(r['avg_latency'] * r['rounds'] for r in d['by_mode']) / d['total_rounds'] / 1000:.1f} <small>s</small></div></div>
</div>

<h2>表 1　按模式统计 <span>token 与延迟</span></h2>
<table>
  <thead>
    <tr>
      <th style="text-align:left">模式</th><th>对话轮数</th><th>平均延迟</th>
      <th>延迟区间</th><th>平均输入 token</th><th>平均输出 token</th>
      <th>平均合计 token</th><th>引导反问率</th>
    </tr>
  </thead>
  <tbody>{''.join(mode_rows)}</tbody>
</table>

<h2>表 2　回复类型分布 <span>用于判定模型是否按预期行为作答</span></h2>
<table>
  <thead><tr><th style="text-align:left">回复类型</th><th>次数</th><th>占比</th></tr></thead>
  <tbody>{type_rows}</tbody>
</table>

<h2>表 3　最近对话明细 <span>最近 {len(d['detail'])} 轮，已省略对话内容</span></h2>
<table>
  <thead><tr><th style="text-align:left">时间</th><th style="text-align:left">模式</th>
  <th style="text-align:left">回复类型</th><th>token</th><th>延迟</th></tr></thead>
  <tbody>{detail_rows}</tbody>
</table>

<div class="note">
  <b>指标说明：</b>「引导反问率」= 陪练模式下回复类型被判定为「引导反问」的轮次 ÷ 陪练总轮次，
  用于客观衡量模型是<b>在引导用户自己定位</b>，而非直接给出答案。该指标将「陪练是否有效」这一主观判断转化为可追踪、可回归的量化数据。
  {'' if not d['failed'] else f'<br>已排除 {d["failed"]} 条失败请求（LLM 超时），不计入延迟与 token 均值。'}
</div>

<div class="foot">生成时间 {datetime.now().strftime('%Y-%m-%d %H:%M')}　|　tools/stats_report.py</div>

</body>
</html>"""


def main() -> int:
    ap = argparse.ArgumentParser(description="埋点统计报表生成器")
    ap.add_argument("--db", default=DB_PATH, help="SQLite 路径")
    ap.add_argument("--out", default=DEFAULT_OUT, help="HTML 输出路径")
    ap.add_argument("--include-failed", action="store_true", help="把失败请求也计入统计")
    ap.add_argument("--stdout", action="store_true", help="只在终端输出，不生成 HTML")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print(f"[错误] 数据库不存在: {args.db}")
        print("       先启动一次 app.py 产生对话数据，或确认路径是否正确")
        return 1

    d = collect(args.db, args.include_failed)
    if not d["total_rounds"]:
        print("[错误] messages 表里没有可统计的助理回复记录")
        return 1

    print_console(d, args.include_failed)
    if args.stdout:
        return 0

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(build_html(d, args.include_failed))
    print(f"报表已生成: {args.out}")
    print("用浏览器打开后截图即可（建议截到表格区域，宽度 1000px）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
