# -*- coding: utf-8 -*-
"""清洗 cases_raw.json → cases_clean.json + cases_clean.csv
保留 10 条试跑用 --limit 10；全量跑不传 --limit。"""
import argparse
import csv
import json
import os
import re
from pathlib import Path

RAW = Path(__file__).parent / "cases_raw.json"
OUT_JSON = Path(__file__).parent / "cases_clean.json"
OUT_CSV = Path(__file__).parent / "cases_clean.csv"

# 字段裁剪（原始 JSON 有 25 个字段，只留 Agent 关心的 12 个）
KEEP_FIELDS = [
    "case_id",        # 用例唯一标识
    "case_name",      # 标题
    "priority",       # P0/P1/P2
    "label",          # 标签（smoke/...）
    "catalog_path",   # 分类路径 → 拆成 catalog_tag
    "precondition",   # 前置条件
    "step",           # 步骤
    "expect",         # 预期结果
    "remark",         # 备注（含已知 bug / wiki 链接，Agent 可参考）
    "execute_result", # PASS/FAIL
    "executor_name",  # 执行人
    "lib_name",       # 所属用例库
]


def clean_text(s: str) -> str:
    """把原始用例里的脏格式（多空格、多余换行、wiki 链接）做轻量归一"""
    s = (s or "").strip()
    # 把 wiki URL 替换成 [wiki] 占位（省 token）
    s = re.sub(r"https?://wiki\.tuya-inc\.com[^\s）)]*", "[wiki]", s)
    # 把空行和多余空格归一
    lines = [ln.strip() for ln in s.splitlines() if ln.strip()]
    return "\n".join(lines)


def extract_catalog_tag(catalog_path: str) -> str:
    """从完整路径里抽最后一级目录作为功能标签
    例："所有用例｜基线用例｜IPC嵌入式｜长电用例｜通用功能｜系统功能｜时区修改" → "时区修改" """
    parts = [p.strip() for p in (catalog_path or "").split("｜") if p.strip()]
    return parts[-1] if parts else ""


def transform(raw: dict) -> dict:
    """单条原始 → 清洗后"""
    return {
        "case_id": str(raw.get("case_id", "")),
        "case_name": clean_text(raw.get("case_name", "")),
        "priority": raw.get("priority", ""),
        "label": clean_text(raw.get("label", "")),
        "catalog_tag": extract_catalog_tag(raw.get("catalog_path", "")),
        "lib_name": clean_text(raw.get("lib_name", "")),
        "precondition": clean_text(raw.get("precondition", "")),
        "step": clean_text(raw.get("step", "")),
        "expect": clean_text(raw.get("expect", "")),
        "remark": clean_text(raw.get("remark", "")),
        "execute_result": raw.get("execute_result", ""),
        "executor_name": raw.get("executor_name", ""),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0,
                        help="只取前 N 条做试跑；0 = 全量")
    args = parser.parse_args()

    with open(RAW, encoding="utf-8") as f:
        raw_list = json.load(f)

    if args.limit > 0:
        raw_list = raw_list[: args.limit]

    cleaned = [transform(r) for r in raw_list]

    # 1) 写 JSON（Agent 运行时读）
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, ensure_ascii=False, indent=2)

    # 2) 写 CSV（UTF-8 BOM，Excel 直接打开）
    fieldnames = list(cleaned[0].keys()) if cleaned else []
    with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(cleaned)

    print(f"清洗完成: {len(cleaned)} 条")
    print(f"  JSON → {OUT_JSON}")
    print(f"  CSV  → {OUT_CSV}")

    # 快速校验
    if cleaned:
        sample = cleaned[0]
        print(f"\n样例 case_id={sample['case_id']} name={sample['case_name'][:30]}")
        lens = [len(c["precondition"]) + len(c["step"]) + len(c["expect"]) for c in cleaned]
        print(f"单条字段总字数 最小={min(lens)} 最大={max(lens)} 平均={sum(lens)/len(lens):.0f}")


if __name__ == "__main__":
    main()
