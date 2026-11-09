# -*- coding: utf-8 -*-
"""
CLI 自测入口：
  python -m retrieval.cli init                # 建库 + 示例数据
  python -m retrieval.cli seed                # 只灌入示例数据
  python -m retrieval.cli run --query "OTA升级失败"  # 检索
  python -m retrieval.cli import-csv cases.csv        # 导入真实 CSV
  python -m retrieval.cli benchmark                    # 跑 10 个问题的 benchmark
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 把项目根加到 path 里，让 python -m retrieval.cli 能 import config
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import RETR_DB_PATH, RETR_TOPK_FINAL
from retrieval.db import DEFAULT_DB_PATH, format_module_index_for_prompt, get_conn, import_csv, init_db, rebuild_fts, seed_samples, update_module_index
from retrieval.injector import count_chars, inject
from retrieval.retriever import retrieve


# ============ 10 个 benchmark 问题 ============

BENCHMARK_QUESTIONS = [
    ("OTA 升级过程中断电会怎样", ["OTA升级", "通用功能/系统功能/OTA升级"]),
    ("视频码率和分辨率怎么测", ["通用功能/音视频能力/视频编码"]),
    ("移动侦测误报率高怎么办", ["通用功能/侦测功能/人形过滤"]),
    ("隐私模式开关的边界条件", ["通用功能/系统功能/隐私模式"]),
    ("本地录像存满了会覆盖吗", ["通用功能/系统功能/存储设置"]),
    ("云台预设点回到原点不准", ["云台机功能/云台功能/预设点"]),
    ("夜视下红外灯不亮", ["通用功能/基本设置/日夜切换"]),
    ("SD卡损坏了设备还能用吗", ["通用功能/系统功能/本地录像", "存储设置"]),
    ("产测老化标志位是什么", ["产测用例"]),
    ("AI角色切换失败怎么办", ["带屏IPC/AI二期需求"]),
]


def cmd_init(args):
    db_path = args.db or RETR_DB_PATH or DEFAULT_DB_PATH
    conn = init_db(db_path)
    rows = seed_samples(conn)
    update_module_index(conn)
    print(f"✅ 数据库已初始化: {db_path}")
    print(f"   示例数据: 新增 {rows} 条（已存在的跳过）")
    print(f"   当前总数: {conn.execute('SELECT COUNT(*) FROM cases').fetchone()[0]} 条")


def cmd_seed(args):
    db_path = args.db or RETR_DB_PATH or DEFAULT_DB_PATH
    conn = get_conn(db_path)
    if not Path(db_path).exists() or conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='cases'"
    ).fetchone() is None:
        init_db(db_path)
    rows = seed_samples(conn)
    rebuild_fts(conn)
    update_module_index(conn)
    print(f"✅ 示例数据灌入: 新增 {rows} 条")
    print(f"   数据库总数: {conn.execute('SELECT COUNT(*) FROM cases').fetchone()[0]} 条")


def cmd_run(args):
    db_path = args.db or RETR_DB_PATH or DEFAULT_DB_PATH
    conn = get_conn(db_path)
    query = args.query
    print(f"\n🔍 检索: {query}")
    print("=" * 60)

    result = retrieve(conn, query, topk_final=args.topk or RETR_TOPK_FINAL)

    print(f"分词:       {result['tokens']}")
    print(f"同义词扩展: {result['aliased_tokens']}")
    print(f"目录路由:   {result['module_hits']}")
    print(f"FTS5 查询:  {result['fts_query']}")
    print(f"FTS 召回:   {result['fts_count']} 条")
    print(f"Catalog:    {result['catalog_count']} 条")
    print(f"合并后:     {result['final_count']} 条 (Top{args.topk or RETR_TOPK_FINAL})")
    print()

    # 打印 TopK
    print(f"{'#':<3} {'Score':<8} {'Prio':<5} {'Case ID':<10} {'Case Name'}")
    print("-" * 80)
    for i, c in enumerate(result["final"], 1):
        print(f"{i:<3} {c['final_score']:<8.4f} {c['priority']:<5} {c['case_id']:<10} {c['case_name']}")
    print()

    # 注入 prompt 预览
    prompt_snippet = inject(result["final"])
    est_tokens = count_chars(prompt_snippet)
    print(f"📋 注入 prompt 预览 (估算 {est_tokens} token):")
    print("-" * 60)
    # 只打印前 500 字
    preview = prompt_snippet[:500]
    if len(prompt_snippet) > 500:
        preview += f"\n\n... (省略 {len(prompt_snippet) - 500} 字)"
    print(preview)


def cmd_import(args):
    db_path = args.db or RETR_DB_PATH or DEFAULT_DB_PATH
    conn = get_conn(db_path)
    csv_path = args.csv
    print(f"📥 导入 CSV: {csv_path}")
    print(f"   目标数据库: {db_path}")
    result = import_csv(conn, csv_path, dry_run=args.dry_run)
    if args.dry_run:
        print("   (DRY RUN) 不会真的写入")
    print(f"   读取: {result['read']} 行")
    print(f"   新增: {result['inserted']}, 更新: {result['updated']}, 跳过: {result['skipped']}")
    if result["errors"]:
        print(f"   错误 {len(result['errors'])} 条，前 3 条:")
        for e in result["errors"][:3]:
            print(f"     - {e}")
    print(f"   当前总数: {conn.execute('SELECT COUNT(*) FROM cases').fetchone()[0]} 条")


def cmd_benchmark(args):
    db_path = args.db or RETR_DB_PATH or DEFAULT_DB_PATH
    conn = get_conn(db_path)
    print("🧪 Benchmark: 10 个真实问题")
    print("=" * 70)

    hits = 0
    total_tokens = 0
    rows = []
    for q, expected_cats in BENCHMARK_QUESTIONS:
        result = retrieve(conn, q)
        injected = inject(result["final"])
        est = count_chars(injected)
        total_tokens += est

        # 判断是否命中：Top3 里有任一条的 catalog_path 包含 expected_cat 关键词
        top3 = result["final"][:3]
        hit = False
        hit_cats = []
        for c in top3:
            cp = (c.get("catalog_path") or c.get("catalog_lv1") or "").lower()
            for ec in expected_cats:
                ec_low = ec.lower()
                # 精确匹配 catalog_lv1 或子串
                lv1 = (c.get("catalog_lv1") or "").lower()
                lv2 = (c.get("catalog_lv2") or "").lower()
                if lv1 and lv1 in ec_low or lv2 and lv2 in ec_low or ec_low in cp:
                    hit = True
                    hit_cats.append(lv1 or c.get("catalog_lv1"))
                    break
            if hit:
                break
        if hit:
            hits += 1

        status = "✅" if hit else "❌"
        top_name = top3[0]["case_name"][:30] if top3 else "(无结果)"
        rows.append((status, est, q[:20], expected_cats[0][:20], hit_cats[:1] if hit_cats else ""))
        print(f"{status} [{est:>4}t] Q: {q}")
        print(f"   Top1: {top_name}")
        print(f"   期望目录: {expected_cats[0]}")
        if hit:
            print(f"   命中目录: {hit_cats[0]}")
        print()

    print("=" * 70)
    print(f"📊 命中率: {hits}/{len(BENCHMARK_QUESTIONS)} ({100*hits//len(BENCHMARK_QUESTIONS)}%)")
    print(f"📊 平均 token: {total_tokens // len(BENCHMARK_QUESTIONS)}")
    print(f"📊 最高 token: {max(r[1] for r in rows)}")
    print(f"📊 最低 token: {min(r[1] for r in rows)}")


def main():
    parser = argparse.ArgumentParser(prog="retrieval", description="用例检索 CLI")
    parser.add_argument("--db", help="SQLite 路径（覆盖 config.yaml）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="建库 + 示例数据")
    sub.add_parser("seed", help="只灌入示例数据（不删已有）")

    p_run = sub.add_parser("run", help="检索单个问题")
    p_run.add_argument("--query", "-q", required=True, help="问题")
    p_run.add_argument("--topk", "-k", type=int, help="最终 TopK")

    p_imp = sub.add_parser("import-csv", help="导入 CSV")
    p_imp.add_argument("--csv", required=True, help="CSV 文件路径")
    p_imp.add_argument("--dry-run", action="store_true", help="只读不写")

    sub.add_parser("benchmark", help="跑 10 问题 benchmark")

    args = parser.parse_args()
    {"init": cmd_init, "seed": cmd_seed, "run": cmd_run,
     "import-csv": cmd_import, "benchmark": cmd_benchmark}[args.cmd](args)


if __name__ == "__main__":
    main()
