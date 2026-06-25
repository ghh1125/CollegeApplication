"""从千问高考「志愿推荐」抓取浙江省2026年官方专业代号，写入 admission_plan_2026.province_major_code。

背景：admission_plan_2026 的 major_code 是抓取脚本生成的内部占位键(ENR2026-*)，
不是浙江省教育考试院发布的真实填报代号。真实代号（如"021""037"，逐年会变）只能
从需要登录的千问「志愿推荐」工具里，按学生分数/位次匹配出的院校列表内嵌专业数据中获取。

**这是半自动脚本，需要人工配合扫码登录**：
    1. 运行 `python scripts/fetch_qianwen_2026_major_codes.py --login`
       会打开一个可见的 Chromium 窗口，用千问 App 扫码登录后回到终端按回车。
       登录态保存在 --profile-dir（默认 /tmp/qianwen_profile），之后可复用。
    2. 运行 `python scripts/fetch_qianwen_2026_major_codes.py --scrape`
       自动滚动加载该账号档案下「全部」匹配院校（每所院校的响应已内嵌专业列表+代号），
       直到连续多次没有新学校出现为止。保存到 --out（默认 /tmp/qianwen_major_codes.json）。
    3. 运行 `python scripts/fetch_qianwen_2026_major_codes.py --load <json文件>`
       将抓取结果与 admission_plan_2026 做精确匹配（学校名 + 专业名+备注完整拼接）
       并写入 province_major_code 列。

注意事项：
    - 千问账号的选科组合会限制能看到的专业范围（物理/化学/生物 组合基本覆盖理工科 +
      大部分文科专业，实测命中率约96%；纯文科类专业可能需要换一个文科选科档案补抓）。
    - 自动化操作账号有平台风险，请用自己的账号、适度控制抓取频率。
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "zhejiang" / "college.db"
DEFAULT_PROFILE_DIR = "/tmp/qianwen_profile"
DEFAULT_OUT = "/tmp/qianwen_major_codes.json"
ZHIYUAN_URL = (
    "https://p.qianwen.com/gaokaopc/zhiyuan-recommend"
    "?uc_param_str=dnntnwvepffrgibijbprsvdsdicheiniutstkp&entry=gaokao_pindao"
)


def cmd_login(profile_dir: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=profile_dir, headless=False,
            viewport={"width": 1200, "height": 900},
        )
        page = browser.new_page()
        page.goto(ZHIYUAN_URL, wait_until="networkidle", timeout=30_000)
        print("浏览器已打开，请用千问 App 扫码登录，并确认能看到个人分数/位次后按回车…")
        input()
        browser.close()
    print("登录态已保存到", profile_dir)


def cmd_scrape(profile_dir: str, out_path: str, max_iter: int, stall_limit: int) -> None:
    from playwright.sync_api import sync_playwright

    captured: list[str] = []

    def on_response(resp):
        try:
            if "getAiRecData" in resp.url:
                captured.append(resp.text())
        except Exception:
            pass

    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=profile_dir, headless=False,
            viewport={"width": 1200, "height": 900},
        )
        page = browser.new_page()
        page.on("response", on_response)
        page.goto(ZHIYUAN_URL, wait_until="networkidle", timeout=30_000)
        page.wait_for_timeout(2000)
        page.get_by_text("全部", exact=False).first.click(timeout=8000)
        page.wait_for_timeout(2000)

        prev = -1
        stall = 0
        for i in range(max_iter):
            page.mouse.wheel(0, 4000)
            page.wait_for_timeout(700)
            if i % 15 == 0:
                schools = _unique_school_ids(captured)
                print(f"滚动{i}次, 响应{len(captured)}, 学校{len(schools)}")
                if len(schools) == prev:
                    stall += 1
                    if stall >= stall_limit:
                        print(f"连续{stall_limit}次无新增，停止于第{i}次")
                        break
                else:
                    stall = 0
                prev = len(schools)
        browser.close()

    with open(out_path, "w", encoding="utf-8") as f:
        for body in captured:
            f.write(body + "\n")
    print(f"完成，共{len(captured)}批响应，已保存到 {out_path}")


def _unique_school_ids(bodies: list[str]) -> set[str]:
    ids: set[str] = set()
    for body in bodies:
        try:
            for c in json.loads(body).get("data", {}).get("colleges", []):
                ids.add(c.get("new_college_id"))
        except Exception:
            pass
    return ids


def _parse_records(jsonl_path: str) -> list[dict]:
    records: list[dict] = []
    seen_schools: set[str] = set()
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            for c in data.get("data", {}).get("colleges", []):
                cid = c.get("new_college_id")
                if cid in seen_schools:
                    continue
                seen_schools.add(cid)
                sname = c.get("name")
                for grp in c.get("major_groups", []):
                    majors = grp.get("majors", {})
                    for bucket in ("recommend", "match"):
                        for m in majors.get(bucket, []):
                            records.append({
                                "school_name": sname,
                                "major_name": m.get("major_name") or m.get("major"),
                                "major_remark": m.get("major_remark", ""),
                                "major_code": m.get("major_code"),
                            })
    return records


def _light_norm(s: str) -> str:
    s = (s or "").replace("（", "(").replace("）", ")")
    return re.sub(r"\s+", "", s).strip()


def cmd_load(jsonl_path: str) -> None:
    records = _parse_records(jsonl_path)
    print(f"解析出 {len(records)} 条专业代号记录")

    conn = sqlite3.connect(str(DB_PATH))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(admission_plan_2026)")}
    if "province_major_code" not in cols:
        conn.execute("ALTER TABLE admission_plan_2026 ADD COLUMN province_major_code TEXT")

    qw_exact: dict[tuple[str, str], str] = {}
    for r in records:
        full = (r["major_name"] or "") + (r["major_remark"] or "")
        key = (r["school_name"], _light_norm(full))
        qw_exact[key] = r["major_code"]

    db_rows = conn.execute("SELECT id, school_name, major_name FROM admission_plan_2026").fetchall()
    hit = 0
    for rid, sn, mn in db_rows:
        key = (sn, _light_norm(mn))
        code = qw_exact.get(key)
        if code:
            conn.execute(
                "UPDATE admission_plan_2026 SET province_major_code=? WHERE id=?", (code, rid)
            )
            hit += 1
    conn.commit()
    print(f"精确匹配并写入: {hit}/{len(db_rows)} ({hit/len(db_rows)*100:.1f}%)")
    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile-dir", default=DEFAULT_PROFILE_DIR)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--login", action="store_true", help="打开浏览器扫码登录")
    parser.add_argument("--scrape", action="store_true", help="滚动抓取全部院校专业代号")
    parser.add_argument("--load", metavar="JSONL", help="解析抓取结果并写入数据库")
    parser.add_argument("--max-iter", type=int, default=500)
    parser.add_argument("--stall-limit", type=int, default=4)
    args = parser.parse_args()

    if args.login:
        cmd_login(args.profile_dir)
    elif args.scrape:
        cmd_scrape(args.profile_dir, args.out, args.max_iter, args.stall_limit)
    elif args.load:
        cmd_load(args.load)
    else:
        parser.print_help()


if __name__ == "__main__":
    sys.path.insert(0, str(BASE_DIR))
    main()
