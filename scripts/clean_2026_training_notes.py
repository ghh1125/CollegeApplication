"""一次性清洗 admission_plan_2026.major_name 里混入的培养安排说明。

千问抓取源把"第X学年在XX校区..."、"要求高考外语成绩不低于X分"这类备注直接
拼进了专业名称，但 2023-2025 历年数据从不包含这类信息，导致同一个延续多年
的老专业因为多了这段说明而文本对不上历史记录，被误判为"新专业/无历史位次"。

用 src.zhejiang.rank_utils.strip_training_notes() 把这两类**安全可剥离**的
备注（整个括号内容只有校区/学年安排或外语门槛，没有混杂"含XX专业"等子方向
区分信息）挪到新增的 training_note 列，major_name 留下干净的专业本体。

幂等：重复运行不会重复剥离（cleaned major_name 不再含这类括号）。
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "zhejiang" / "college.db"

sys.path.insert(0, str(BASE_DIR))
from src.zhejiang.rank_utils import strip_training_notes  # noqa: E402


def clean_training_notes(db_path: Path = DB_PATH) -> int:
    conn = sqlite3.connect(str(db_path))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(admission_plan_2026)")}
    if "training_note" not in cols:
        conn.execute("ALTER TABLE admission_plan_2026 ADD COLUMN training_note TEXT")

    rows = conn.execute("SELECT id, major_name FROM admission_plan_2026").fetchall()
    changed = 0
    for rid, mn in rows:
        cleaned, note = strip_training_notes(mn)
        if note:
            conn.execute(
                "UPDATE admission_plan_2026 SET major_name=?, training_note=? WHERE id=?",
                (cleaned, note, rid),
            )
            changed += 1
    conn.commit()
    conn.close()
    return changed


if __name__ == "__main__":
    changed = clean_training_notes()
    print(f"清洗记录数: {changed}")
