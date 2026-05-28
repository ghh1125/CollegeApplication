"""
Scrape school province+city from static-data.gaokao.cn.

Fetches school info for IDs 1–4000 concurrently, extracts
province_id / city_id / name / address, then:
  1. Derives city name from city_id using GB/T 2260 codes.
  2. Upserts results into school_master (school_name, province, city).

Run:  python3 scripts/scrape_school_locations.py
"""

from __future__ import annotations

import json
import re
import ssl
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional
from urllib.request import urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ── Province ID → name (GB/T 2260) ───────────────────────────────────────────

PROVINCE_ID_MAP: dict[str, str] = {
    "11": "北京",  "12": "天津",  "13": "河北",  "14": "山西",  "15": "内蒙古",
    "21": "辽宁",  "22": "吉林",  "23": "黑龙江",
    "31": "上海",  "32": "江苏",  "33": "浙江",  "34": "安徽",
    "35": "福建",  "36": "江西",  "37": "山东",
    "41": "河南",  "42": "湖北",  "43": "湖南",
    "44": "广东",  "45": "广西",  "46": "海南",
    "50": "重庆",  "51": "四川",  "52": "贵州",  "53": "云南",  "54": "西藏",
    "61": "陕西",  "62": "甘肃",  "63": "青海",  "64": "宁夏",  "65": "新疆",
}

# ── City ID → name (GB/T 2260, prefecture-level cities with universities) ────
# Format: province_code (2-digit) + city_seq (2-digit)
# Direct municipalities (北京/天津/上海/重庆) map XX01 → same as province name.

CITY_ID_MAP: dict[str, str] = {
    # 北京 11
    "1101": "北京",
    # 天津 12
    "1201": "天津",
    # 河北 13
    "1301": "石家庄", "1302": "唐山",    "1303": "秦皇岛", "1304": "邯郸",
    "1305": "邢台",   "1306": "保定",    "1307": "张家口", "1308": "承德",
    "1309": "沧州",   "1310": "廊坊",    "1311": "衡水",
    # 山西 14
    "1401": "太原",   "1402": "大同",    "1403": "阳泉",   "1404": "长治",
    "1405": "晋城",   "1406": "朔州",    "1407": "晋中",   "1408": "运城",
    "1409": "忻州",   "1410": "临汾",    "1411": "吕梁",
    # 内蒙古 15
    "1501": "呼和浩特", "1502": "包头",  "1503": "乌海",   "1504": "赤峰",
    "1505": "通辽",   "1506": "鄂尔多斯", "1507": "呼伦贝尔", "1508": "巴彦淖尔",
    "1509": "乌兰察布", "1522": "兴安盟", "1525": "锡林郭勒盟", "1529": "阿拉善盟",
    # 辽宁 21
    "2101": "沈阳",   "2102": "大连",    "2103": "鞍山",   "2104": "抚顺",
    "2105": "本溪",   "2106": "丹东",    "2107": "锦州",   "2108": "营口",
    "2109": "阜新",   "2110": "辽阳",    "2111": "盘锦",   "2112": "铁岭",
    "2113": "朝阳",   "2114": "葫芦岛",
    # 吉林 22
    "2201": "长春",   "2202": "吉林",    "2203": "四平",   "2204": "辽源",
    "2205": "通化",   "2206": "白山",    "2207": "松原",   "2208": "白城",
    "2224": "延边",
    # 黑龙江 23
    "2301": "哈尔滨", "2302": "齐齐哈尔", "2303": "鸡西",  "2304": "鹤岗",
    "2305": "双鸭山", "2306": "大庆",    "2307": "伊春",   "2308": "佳木斯",
    "2309": "七台河", "2310": "牡丹江",  "2311": "黑河",   "2312": "绥化",
    "2327": "大兴安岭",
    # 上海 31
    "3101": "上海",
    # 江苏 32
    "3201": "南京",   "3202": "无锡",    "3203": "徐州",   "3204": "常州",
    "3205": "苏州",   "3206": "南通",    "3207": "连云港", "3208": "淮安",
    "3209": "盐城",   "3210": "扬州",    "3211": "镇江",   "3212": "泰州",
    "3213": "宿迁",
    # 浙江 33
    "3301": "杭州",   "3302": "宁波",    "3303": "温州",   "3304": "嘉兴",
    "3305": "湖州",   "3306": "绍兴",    "3307": "金华",   "3308": "衢州",
    "3309": "舟山",   "3310": "台州",    "3311": "丽水",
    # 安徽 34
    "3401": "合肥",   "3402": "芜湖",    "3403": "蚌埠",   "3404": "淮南",
    "3405": "马鞍山", "3406": "淮北",    "3407": "铜陵",   "3408": "安庆",
    "3410": "黄山",   "3411": "滁州",    "3412": "阜阳",   "3413": "宿州",
    "3415": "六安",   "3416": "亳州",    "3417": "池州",   "3418": "宣城",
    # 福建 35
    "3501": "福州",   "3502": "厦门",    "3503": "莆田",   "3504": "三明",
    "3505": "泉州",   "3506": "漳州",    "3507": "南平",   "3508": "龙岩",
    "3509": "宁德",
    # 江西 36
    "3601": "南昌",   "3602": "景德镇",  "3603": "萍乡",   "3604": "九江",
    "3605": "新余",   "3606": "鹰潭",    "3607": "赣州",   "3608": "吉安",
    "3609": "宜春",   "3610": "抚州",    "3611": "上饶",
    # 山东 37
    "3701": "济南",   "3702": "青岛",    "3703": "淄博",   "3704": "枣庄",
    "3705": "东营",   "3706": "烟台",    "3707": "潍坊",   "3708": "济宁",
    "3709": "泰安",   "3710": "威海",    "3711": "日照",   "3713": "临沂",
    "3714": "德州",   "3715": "聊城",    "3716": "滨州",   "3717": "菏泽",
    # 河南 41
    "4101": "郑州",   "4102": "开封",    "4103": "洛阳",   "4104": "平顶山",
    "4105": "安阳",   "4106": "鹤壁",    "4107": "新乡",   "4108": "焦作",
    "4109": "濮阳",   "4110": "许昌",    "4111": "漯河",   "4112": "三门峡",
    "4113": "南阳",   "4114": "商丘",    "4115": "信阳",   "4116": "周口",
    "4117": "驻马店",
    # 湖北 42
    "4201": "武汉",   "4202": "黄石",    "4203": "十堰",   "4205": "宜昌",
    "4206": "襄阳",   "4207": "鄂州",    "4208": "荆门",   "4209": "孝感",
    "4210": "荆州",   "4211": "黄冈",    "4212": "咸宁",   "4213": "随州",
    "4228": "恩施",   "4290": "仙桃",    "4291": "潜江",   "4292": "天门",
    # 湖南 43
    "4301": "长沙",   "4302": "株洲",    "4303": "湘潭",   "4304": "衡阳",
    "4305": "邵阳",   "4306": "岳阳",    "4307": "常德",   "4308": "张家界",
    "4309": "益阳",   "4310": "郴州",    "4311": "永州",   "4312": "怀化",
    "4313": "娄底",   "4331": "湘西",
    # 广东 44
    "4401": "广州",   "4402": "韶关",    "4403": "深圳",   "4404": "珠海",
    "4405": "汕头",   "4406": "佛山",    "4407": "江门",   "4408": "湛江",
    "4409": "茂名",   "4412": "肇庆",    "4413": "惠州",   "4414": "梅州",
    "4415": "汕尾",   "4416": "河源",    "4417": "阳江",   "4418": "清远",
    "4419": "东莞",   "4420": "中山",    "4451": "潮州",   "4452": "揭州",
    "4453": "云浮",
    # 广西 45
    "4501": "南宁",   "4502": "柳州",    "4503": "桂林",   "4504": "梧州",
    "4505": "北海",   "4506": "防城港",  "4507": "钦州",   "4508": "贵港",
    "4509": "玉林",   "4510": "百色",    "4511": "贺州",   "4512": "河池",
    "4513": "来宾",   "4514": "崇左",
    # 海南 46
    "4601": "海口",   "4602": "三亚",    "4603": "三沙",   "4604": "儋州",
    # 重庆 50
    "5001": "重庆",
    # 四川 51
    "5101": "成都",   "5103": "自贡",    "5104": "攀枝花", "5105": "泸州",
    "5106": "德阳",   "5107": "绵阳",    "5108": "广元",   "5109": "遂宁",
    "5110": "内江",   "5111": "乐山",    "5113": "南充",   "5114": "眉山",
    "5115": "宜宾",   "5116": "广安",    "5117": "达州",   "5118": "雅安",
    "5119": "巴中",   "5120": "资阳",    "5132": "阿坝",   "5133": "甘孜",
    "5134": "凉山",
    # 贵州 52
    "5201": "贵阳",   "5202": "六盘水",  "5203": "遵义",   "5204": "安顺",
    "5205": "毕节",   "5206": "铜仁",    "5223": "黔西南", "5226": "黔东南",
    "5227": "黔南",
    # 云南 53
    "5301": "昆明",   "5303": "曲靖",    "5304": "玉溪",   "5305": "保山",
    "5306": "昭通",   "5307": "丽江",    "5308": "普洱",   "5309": "临沧",
    "5323": "楚雄",   "5325": "红河",    "5326": "文山",   "5328": "西双版纳",
    "5329": "大理",   "5331": "德宏",    "5333": "怒江",   "5334": "迪庆",
    # 西藏 54
    "5401": "拉萨",   "5402": "日喀则",  "5403": "昌都",   "5404": "林芝",
    "5405": "山南",   "5406": "那曲",    "5425": "阿里",
    # 陕西 61
    "6101": "西安",   "6102": "铜川",    "6103": "宝鸡",   "6104": "咸阳",
    "6105": "渭南",   "6106": "延安",    "6107": "汉中",   "6108": "榆林",
    "6109": "安康",   "6110": "商洛",
    # 甘肃 62
    "6201": "兰州",   "6202": "嘉峪关",  "6203": "金昌",   "6204": "白银",
    "6205": "天水",   "6206": "武威",    "6207": "张掖",   "6208": "平凉",
    "6209": "酒泉",   "6210": "庆阳",    "6211": "定西",   "6212": "陇南",
    "6229": "临夏",   "6230": "甘南",
    # 青海 63
    "6301": "西宁",   "6302": "海东",    "6321": "海北",   "6322": "黄南",
    "6323": "海南",   "6324": "果洛",    "6325": "玉树",   "6326": "海西",
    # 宁夏 64
    "6401": "银川",   "6402": "石嘴山",  "6403": "吴忠",   "6404": "固原",
    "6405": "中卫",
    # 新疆 65
    "6501": "乌鲁木齐", "6502": "克拉玛依", "6504": "吐鲁番", "6505": "哈密",
    "6522": "昌吉",   "6523": "博州",    "6524": "巴州",   "6525": "阿克苏",
    "6526": "克州",   "6527": "喀什",    "6528": "和田",   "6532": "伊犁",
    "6540": "塔城",   "6542": "阿勒泰",  "6590": "石河子",
}

SSL_CTX = ssl._create_unverified_context()
BASE_URL = "https://static-data.gaokao.cn/www/2.0/school/{}/info.json"


def _fetch_school(school_id: int) -> Optional[dict]:
    """Fetch one school's info; return None if not found."""
    url = BASE_URL.format(school_id)
    try:
        with urlopen(url, timeout=8, context=SSL_CTX) as r:
            data = json.loads(r.read())
        if data.get("code") != "0000":
            return None
        d = data["data"]
        ruanke = d.get("ruanke_rank", "0")
        try:
            ruanke_int = int(ruanke) if ruanke and str(ruanke) != "0" else None
        except (ValueError, TypeError):
            ruanke_int = None
        return {
            "school_id":   d.get("school_id", ""),
            "name":        d.get("name", ""),
            "province_id": d.get("province_id", ""),
            "city_id":     d.get("city_id", ""),
            "address":     d.get("address", ""),
            "ruanke_rank": ruanke_int,
        }
    except Exception:
        return None


def _extract_city_from_address(address: str) -> Optional[str]:
    """Extract city name (XX市) from address string."""
    # Take the first campus segment (before first comma)
    segment = address.split(",")[0].split("，")[0]
    # Standard format: XX省YY市ZZ区... or 直辖市: 北京市XX区...
    m = re.search(r'[省市]([^\s省市县区路号]+市)', segment)
    if m:
        return m.group(1).rstrip("市")
    # Direct municipality: 北京市/上海市/天津市/重庆市 at start
    m2 = re.match(r'^(北京|上海|天津|重庆)市', segment)
    if m2:
        return m2.group(1)
    return None


def scrape_all(max_id: int = 4000, workers: int = 40) -> list[dict]:
    """Scan IDs 1..max_id in parallel, collect valid school records."""
    results = []
    total = max_id

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_fetch_school, i): i for i in range(1, max_id + 1)}
        done = 0
        for fut in as_completed(futures):
            done += 1
            if done % 500 == 0:
                print(f"  {done}/{total} scanned, {len(results)} found …")
            r = fut.result()
            if r and r["name"]:
                results.append(r)

    return results


def main() -> None:
    print("Scanning gaokao.cn school IDs 1–4000 …")
    t0 = time.time()
    schools = scrape_all(max_id=4000, workers=50)
    elapsed = time.time() - t0
    print(f"Done: {len(schools)} schools found in {elapsed:.1f}s")

    # Build city_id → city_name from addresses (fills gaps in CITY_ID_MAP)
    extra_city_map: dict[str, str] = {}
    for s in schools:
        cid = s["city_id"]
        if cid not in CITY_ID_MAP and cid not in extra_city_map:
            city = _extract_city_from_address(s["address"])
            if city:
                extra_city_map[cid] = city

    city_map = {**CITY_ID_MAP, **extra_city_map}
    print(f"City ID map: {len(CITY_ID_MAP)} hardcoded + {len(extra_city_map)} from addresses")

    # Save raw JSON for inspection
    out_path = PROJECT_ROOT / "data" / "raw" / "school_locations_raw.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(schools, f, ensure_ascii=False, indent=2)
    print(f"Raw data saved to {out_path}")

    # Upsert into school_master
    from app.db import get_conn, get_cursor

    # Ensure ruanke_rank column exists
    with get_conn() as conn:
        with get_cursor(conn) as cur:
            cur.execute("PRAGMA table_info(school_master)")
            cols = {row[1] for row in cur.fetchall()}
            if "ruanke_rank" not in cols:
                cur.execute("ALTER TABLE school_master ADD COLUMN ruanke_rank INTEGER")
                print("Added ruanke_rank column to school_master")

    upsert_sql = """
        INSERT INTO school_master (school_code, school_name, province, city, ruanke_rank)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (school_code) DO UPDATE SET
            province    = EXCLUDED.province,
            city        = EXCLUDED.city,
            ruanke_rank = EXCLUDED.ruanke_rank
    """

    inserted = 0
    no_city = []
    with get_conn() as conn:
        with get_cursor(conn) as cur:
            for s in schools:
                prov = PROVINCE_ID_MAP.get(s["province_id"], "")
                city = city_map.get(s["city_id"], "")
                if not city:
                    no_city.append((s["name"], s["city_id"]))
                cur.execute(upsert_sql, (s["name"], s["name"], prov, city, s.get("ruanke_rank")))
                inserted += 1
    print(f"Upserted {inserted} schools into school_master")

    if no_city:
        print(f"WARNING: {len(no_city)} schools had unknown city_id:")
        for name, cid in no_city[:20]:
            print(f"  {name}: city_id={cid}")

    # Coverage check
    with get_conn() as conn:
        with get_cursor(conn) as cur:
            cur.execute("""
                SELECT COUNT(DISTINCT ap.school_name)
                FROM admission_plan ap
                JOIN school_master sm ON sm.school_name = ap.school_name
                WHERE sm.city IS NOT NULL AND sm.city != ''
            """)
            covered = cur.fetchone()[0]
            cur.execute("SELECT COUNT(DISTINCT school_name) FROM admission_plan")
            total = cur.fetchone()[0]
    print(f"admission_plan city coverage: {covered}/{total} ({covered/total*100:.1f}%)")


if __name__ == "__main__":
    main()
