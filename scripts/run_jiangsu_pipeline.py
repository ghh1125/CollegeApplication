"""Run the Jiangsu data pipeline end to end.

Default mode is offline-safe: it parses existing plan sources, rebuilds the DB,
and audits coverage. Use --fetch-official or --fetch-plan-sources when network
collection is needed.

Examples:
    uv run python scripts/run_jiangsu_pipeline.py
    uv run python scripts/run_jiangsu_pipeline.py --fetch-official
    uv run python scripts/run_jiangsu_pipeline.py --fetch-plan-sources --limit 50
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_YEARS = (2025, 2024, 2023)


def run(args: list[str]) -> None:
    print("$ " + " ".join(args))
    subprocess.run(args, cwd=PROJECT_ROOT, check=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, nargs="+", default=list(DEFAULT_YEARS))
    parser.add_argument("--fetch-official", action="store_true", help="重新抓取江苏官方投档线/逐分段")
    parser.add_argument("--fetch-plan-sources", action="store_true", help="搜索并下载公开招生计划源")
    parser.add_argument("--limit", type=int, default=0, help="抓取计划源时限制学校数量，0=全部")
    parser.add_argument("--school", action="append", default=[], help="抓取计划源时只抓指定学校名，可重复")
    parser.add_argument("--url", action="append", default=[], help="直接下载指定计划源 URL，可重复")
    parser.add_argument("--max-results", type=int, default=6, help="每所学校搜索结果数量")
    parser.add_argument("--delay", type=float, default=1.0, help="公开源抓取请求间隔")
    parser.add_argument("--allow-zsgk", action="store_true", help="公开源抓取允许掌上高考域名")
    args = parser.parse_args()

    year_args = [str(y) for y in args.years]
    py = [sys.executable]

    if args.fetch_official:
        run(py + ["scripts/fetch_jiangsu_official.py", "--years", *year_args])

    if args.fetch_plan_sources:
        cmd = py + [
            "scripts/fetch_jiangsu_plan_sources.py",
            "--years",
            *year_args,
            "--max-results",
            str(args.max_results),
            "--delay",
            str(args.delay),
        ]
        if args.limit:
            cmd.extend(["--limit", str(args.limit)])
        for school in args.school:
            cmd.extend(["--school", school])
        for url in args.url:
            cmd.extend(["--url", url])
        if args.allow_zsgk:
            cmd.append("--allow-zsgk")
        run(cmd)

    run(py + ["scripts/parse_jiangsu_plan_details.py", "--years", *year_args])
    run(py + ["-m", "src.jiangsu.input.ingest"])
    run(py + ["scripts/audit_jiangsu_plan_coverage.py"])


if __name__ == "__main__":
    main()
