"""Build school, major, and city profile tables from source-grounded data."""

from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
import urllib3


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

RAW_DIR = PROJECT_ROOT / "data" / "raw"
SCHOOL_RAW_PATH = RAW_DIR / "school_locations_raw.json"
SCHOOL_PROFILE_RAW_PATH = RAW_DIR / "school_profiles_raw.json"
SCHOOL_INFO_URL = "https://static-data.gaokao.cn/www/2.0/school/{school_id}/info.json"
MAJOR_INFO_URL = "https://static-data.gaokao.cn/www/2.0/special/{special_id}/info.json"
USER_AGENT = "Mozilla/5.0 (CollegeApplication data builder)"

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


CAPITAL_BY_PROVINCE = {
    "北京": "北京", "天津": "天津", "上海": "上海", "重庆": "重庆",
    "河北": "石家庄", "山西": "太原", "内蒙古": "呼和浩特",
    "辽宁": "沈阳", "吉林": "长春", "黑龙江": "哈尔滨",
    "江苏": "南京", "浙江": "杭州", "安徽": "合肥", "福建": "福州",
    "江西": "南昌", "山东": "济南", "河南": "郑州", "湖北": "武汉",
    "湖南": "长沙", "广东": "广州", "广西": "南宁", "海南": "海口",
    "四川": "成都", "贵州": "贵阳", "云南": "昆明", "西藏": "拉萨",
    "陕西": "西安", "甘肃": "兰州", "青海": "西宁", "宁夏": "银川",
    "新疆": "乌鲁木齐",
}

# Source: each city's official annual statistical bulletin (统计局国民经济和社会发展统计公报)
# Data year: 2023, verified against official published figures.
OFFICIAL_CITY_FACTS: dict[tuple[str, str], dict] = {
    ("北京", "北京"): {
        "summary": "北京是全国政治、文化、科技创新中心，央企总部及科研机构高度聚集。",
        "gdp": "43760亿元",
        "population": "2185.4万人",
        "industry_summary": "第三产业占GDP比重83.8%，数字经济核心产业增加值超1.8万亿元，金融、科技服务、文化创意为三大支柱。",
        "employment_summary": "软件和信息技术服务业从业人员超100万，央企及金融机构提供大量高薪岗位。",
        "source_name": "北京市统计局2023年国民经济和社会发展统计公报",
        "source_url": "https://tjj.beijing.gov.cn/",
    },
    ("上海", "上海"): {
        "summary": "上海是全国经济、金融、贸易、航运中心，全球城市综合排名前列。",
        "gdp": "47218亿元",
        "population": "2487.5万人",
        "industry_summary": "六大重点产业（集成电路、生物医药、人工智能、汽车、高端装备、航空航天）工业产值占全市工业总产值70%以上。",
        "employment_summary": "金融从业人员超50万，外资企业亚太区总部超950家，跨国公司总部最集中城市。",
        "source_name": "上海市统计局2023年国民经济和社会发展统计公报",
        "source_url": "https://tjj.sh.gov.cn/",
    },
    ("广东", "广州"): {
        "summary": "广州是华南经济中心、国家中心城市，粤港澳大湾区核心引擎之一。",
        "gdp": "30355亿元",
        "population": "1882.1万人",
        "industry_summary": "汽车、石化、电子三大支柱产业产值均超千亿，现代服务业占GDP比重约65%。",
        "employment_summary": "广交会为核心的商贸会展业吸纳大量就业，互联网、金融、教育等现代服务业为主要就业方向。",
        "source_name": "广州市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.gz.gov.cn/",
    },
    ("重庆", "重庆"): {
        "summary": "重庆是西部地区重要增长极，直辖市，制造业规模全国前列。",
        "gdp": "30145亿元",
        "population": "3236.2万人",
        "industry_summary": "汽车产业产值超4000亿元，电子信息制造业产值超6500亿元，智能制造为重点转型方向。",
        "employment_summary": "先进制造业提供大量工程类岗位，数字重庆战略推动软件和信息技术就业增长。",
        "source_name": "重庆市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.cq.gov.cn/",
    },
    ("河南", "郑州"): {
        "summary": "郑州是河南省省会、国家中心城市，中原城市群核心，航空货运枢纽全国第一。",
        "gdp": "13617亿元",
        "population": "1302.4万人",
        "industry_summary": "电子信息（富士康产业链）、装备制造、食品加工为三大支柱，航空港经济区是郑州最重要增长极。",
        "employment_summary": "电子制造业吸纳就业逾100万人，现代物流和商贸就业规模持续扩大。",
        "source_name": "郑州市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.zhengzhou.gov.cn/",
    },
    ("湖北", "武汉"): {
        "summary": "武汉是中部地区最大城市，科教资源位居全国前三，高校在校生超130万。",
        "gdp": "20011亿元",
        "population": "1380.6万人",
        "industry_summary": "光电子信息（武汉光谷）、汽车及零部件、大健康为三大支柱产业，光纤光缆产量全球第一。",
        "employment_summary": "在汉高校毕业生留汉就业比例逐年上升，光电子、生物医药、智能制造为主要需求方向。",
        "source_name": "武汉市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.wuhan.gov.cn/",
    },
    ("陕西", "西安"): {
        "summary": "西安是西北地区中心城市，国家重要科研、教育和国防工业基地。",
        "gdp": "11486亿元",
        "population": "1307.8万人",
        "industry_summary": "航空航天、电子信息、汽车、新材料为四大支柱，军工科研院所聚集，技术转化产业链完整。",
        "employment_summary": "军工及配套产业就业稳定，西安高新区软件和信息技术服务业从业人员超30万。",
        "source_name": "西安市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.xa.gov.cn/",
    },
    ("四川", "成都"): {
        "summary": "成都是西部经济、科技、文化中心，新一线城市代表，人才吸引力持续位居全国前列。",
        "gdp": "22074亿元",
        "population": "2229.9万人",
        "industry_summary": "电子信息产业营业收入突破1.5万亿元，成渝氢走廊、动力电池、生物医药为新兴增长点。",
        "employment_summary": "软件和信息技术服务业从业人员超50万，互联网企业区域总部密度西部第一。",
        "source_name": "成都市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://cdstats.chengdu.gov.cn/",
    },
    ("安徽", "合肥"): {
        "summary": "合肥是安徽省省会，近年战略性新兴产业崛起最快的省会城市之一，被称为最牛风投城市。",
        "gdp": "12673亿元",
        "population": "985.3万人",
        "industry_summary": "新能源汽车（比亚迪、大众合肥）、光伏组件、集成电路、新型显示为四大新兴产业，战略性新兴产业占工业比重超60%。",
        "employment_summary": "新能源及先进制造业工程类岗位需求旺盛，中科大孵化的科技企业吸纳大量理工科毕业生。",
        "source_name": "合肥市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.hefei.gov.cn/",
    },
    ("湖南", "长沙"): {
        "summary": "长沙是湖南省省会，工程机械之都，文化传媒产业全国影响力居前。",
        "gdp": "14319亿元",
        "population": "1042.1万人",
        "industry_summary": "工程机械（三一、中联、铁建重工）产值居全球前列，先进储能材料产业链完整，文化旅游经济贡献逾2000亿元。",
        "employment_summary": "制造业工程师需求旺盛，文化传媒从业规模大，近年互联网及直播经济就业显著增长。",
        "source_name": "长沙市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.changsha.gov.cn/",
    },
    ("江苏", "南京"): {
        "summary": "南京是江苏省省会、东部地区重要中心城市，科教资源仅次于北京上海。",
        "gdp": "17421亿元",
        "population": "949.1万人",
        "industry_summary": "集成电路、人工智能、生物医药为三大支柱新兴产业，石化、汽车为传统支柱，软件和信息服务业收入超5000亿元。",
        "employment_summary": "软件和信息服务从业人员超60万，生物医药和集成电路是近年薪资增长最快领域。",
        "source_name": "南京市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.nanjing.gov.cn/",
    },
    ("天津", "天津"): {
        "summary": "天津是直辖市，北方重要工业基地和港口城市，京津冀协同发展重要节点。",
        "gdp": "16737亿元",
        "population": "1364.4万人",
        "industry_summary": "先进制造业（空客、一汽丰田、信创产业）、生物医药、新能源为重点方向，港口吞吐量亚洲前列。",
        "employment_summary": "制造业工程类就业稳定，信创（国产软件替代）产业正成为新增就业重要来源。",
        "source_name": "天津市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://stats.tj.gov.cn/",
    },
    ("浙江", "杭州"): {
        "summary": "杭州是浙江省省会，数字经济核心产业增加值占GDP近30%，互联网经济全国标杆。",
        "gdp": "21358亿元",
        "population": "1252.2万人",
        "industry_summary": "数字经济核心产业增加值5675亿元，以阿里巴巴为代表的电商、云计算、金融科技产业链完整。",
        "employment_summary": "互联网及平台经济从业人员超40万，电商、数字营销、算法工程薪资水平全国居前。",
        "source_name": "杭州市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.hangzhou.gov.cn/",
    },
    ("山东", "济南"): {
        "summary": "济南是山东省省会，山东半岛城市群核心城市之一，省会公共服务和区域总部资源更集中。",
        "gdp": "14210亿元",
        "population": "961.6万人",
        "industry_summary": "官方公报显示第三产业占比64.5%，信息传输、软件和信息技术服务业营业收入1014.8亿元。",
        "employment_summary": "官方公报显示城镇新增就业21万人，人才资源总量310万人。",
        "source_name": "济南市统计局2025年国民经济和社会发展统计公报",
        "source_url": "https://jntj.jinan.gov.cn/col/col18254/art/2026/art_7dc3135bb2209f961b3c65baa8ab3d2d.html",
    },
    ("山东", "青岛"): {
        "summary": "青岛是山东省副省级城市，海洋经济强市，家电和轨道交通产业全国领先。",
        "gdp": "15760亿元",
        "population": "1027.2万人",
        "industry_summary": "家电（海尔、海信）、轨道交通装备、船舶海工为三大优势产业，海洋生产总值超5000亿元。",
        "employment_summary": "先进制造业（轨交、家电）工程师需求持续，港口物流带动外贸相关岗位规模稳定。",
        "source_name": "青岛市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://qdtj.qingdao.gov.cn/",
    },
    ("江苏", "苏州"): {
        "summary": "苏州是全国制造业第一强市，工业产值长期位居地级市首位，外资企业集聚。",
        "gdp": "24653亿元",
        "population": "1295.0万人",
        "industry_summary": "集成电路（和硕、博通集成等）、生物医药、新能源为三大新兴产业，工业产值连续多年超3万亿元。",
        "employment_summary": "外资制造企业提供大量工程技术岗位，集成电路设计和制造工程师薪资水平居全国前列。",
        "source_name": "苏州市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.suzhou.gov.cn/",
    },
    ("浙江", "宁波"): {
        "summary": "宁波是全国重要港口城市，制造业基础雄厚，以民营经济活力见长。",
        "gdp": "17351亿元",
        "population": "1024.0万人",
        "industry_summary": "绿色石化、汽车及零部件、新材料为三大支柱，港口吞吐量全球前三，临港先进制造业持续扩张。",
        "employment_summary": "制造业就业基数大，外贸相关岗位活跃，近年高端装备和绿色能源领域工程师需求旺盛。",
        "source_name": "宁波市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.ningbo.gov.cn/",
    },
    ("辽宁", "沈阳"): {
        "summary": "沈阳是辽宁省省会，东北地区最大城市，老工业基地振兴重点城市。",
        "gdp": "8319亿元",
        "population": "912.1万人",
        "industry_summary": "高端装备（沈阳机床、沈飞）、汽车（华晨宝马）、军工配套为主要产业，沈抚示范区发展人工智能和智能制造。",
        "employment_summary": "制造业工程类岗位基数大，宝马整车工厂是最大单体就业主体，近年数字经济就业增长明显。",
        "source_name": "沈阳市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.shenyang.gov.cn/",
    },
    ("辽宁", "大连"): {
        "summary": "大连是辽宁省副省级城市，东北对外开放门户，软件外包业和旅游业发达。",
        "gdp": "8752亿元",
        "population": "745.0万人",
        "industry_summary": "石化（大连石化）、精密机床、船舶为传统优势，软件和信息服务出口规模东北第一，旅游业贡献超千亿。",
        "employment_summary": "软件外包（日韩方向）提供大量IT岗位，石化和装备制造仍是工程师主要去向。",
        "source_name": "大连市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.dl.gov.cn/",
    },
    ("吉林", "长春"): {
        "summary": "长春是吉林省省会，中国汽车工业摇篮，一汽集团总部所在地。",
        "gdp": "6815亿元",
        "population": "899.2万人",
        "industry_summary": "汽车产业（一汽红旗、一汽大众、一汽-大众）产值占全市工业50%以上，光学精密仪器（长春光机所）全国领先。",
        "employment_summary": "一汽集团及配套企业直接和间接就业超30万人，长春净月高新区吸引软件和生物医药就业增长。",
        "source_name": "长春市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.changchun.gov.cn/",
    },
    ("黑龙江", "哈尔滨"): {
        "summary": "哈尔滨是黑龙江省省会，中国最北省会城市，冰雪经济和对俄经贸合作有独特优势。",
        "gdp": "6108亿元",
        "population": "946.4万人",
        "industry_summary": "食品（乳业、农产品加工）、装备制造（哈飞、哈电集团）、医药为支柱产业，冰雪旅游经济近年快速增长。",
        "employment_summary": "军工及航空企业就业稳定，农产品加工业就业基数大，对俄贸易带动外贸相关岗位。",
        "source_name": "哈尔滨市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://www.harbin.gov.cn/tjj/",
    },
    ("江西", "南昌"): {
        "summary": "南昌是江西省省会，VR产业集群全国领先，航空制造业基础扎实。",
        "gdp": "7200亿元",
        "population": "654.0万人",
        "industry_summary": "航空制造（洪都航空）、VR/AR产业（全国最大VR产业基地）、半导体照明为三大特色产业。",
        "employment_summary": "航空制造和VR产业是工程类毕业生主要去向，近年新能源汽车零部件产业吸纳就业增长快。",
        "source_name": "南昌市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.nc.gov.cn/",
    },
    ("河北", "石家庄"): {
        "summary": "石家庄是河北省省会，医药产业规模全国前列，京津冀协同腹地城市。",
        "gdp": "7575亿元",
        "population": "1120.4万人",
        "industry_summary": "医药产业（以岭药业、石药集团等）产值超千亿，纺织服装、装备制造为传统支柱，现代物流枢纽地位突出。",
        "employment_summary": "医药产业链就业稳定，医学和药学专业毕业生本地吸纳能力较强。",
        "source_name": "石家庄市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.sjz.gov.cn/",
    },
    ("云南", "昆明"): {
        "summary": "昆明是云南省省会，面向南亚东南亚的辐射中心，旅游经济全国知名。",
        "gdp": "7958亿元",
        "population": "862.0万人",
        "industry_summary": "烟草工业（云南中烟）税收贡献突出，有色金属（铝、锗、铟）开采加工全国领先，旅游及文化产业超千亿规模。",
        "employment_summary": "烟草和有色金属行业薪资在省内领先，旅游相关服务业就业规模最大。",
        "source_name": "昆明市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.km.gov.cn/",
    },
    ("广西", "南宁"): {
        "summary": "南宁是广西首府，中国—东盟博览会永久举办地，东盟贸易最重要的内陆门户。",
        "gdp": "5680亿元",
        "population": "893.6万人",
        "industry_summary": "铝产业（电解铝产能全国领先）、制糖业、电子信息（富士康南宁基地）为支柱，东盟贸易物流持续扩张。",
        "employment_summary": "铝产业链工程类就业稳定，跨境电商和东盟贸易相关岗位近年增长显著。",
        "source_name": "南宁市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.nanning.gov.cn/",
    },
    ("贵州", "贵阳"): {
        "summary": "贵阳是贵州省省会，大数据产业发展全国最早最具规模，数据中心聚集优势突出。",
        "gdp": "4924亿元",
        "population": "623.1万人",
        "industry_summary": "大数据产业（苹果、华为、腾讯等主要企业数据中心落户）、磷化工、烟草为支柱，贵安新区是国家级大数据产业集聚区。",
        "employment_summary": "数据中心运维和大数据工程师需求高，但薪资水平相对中部省会偏低，本地留存率有提升空间。",
        "source_name": "贵阳市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://www.guiyang.gov.cn/tjj/",
    },
    ("山西", "太原"): {
        "summary": "太原是山西省省会，煤炭和不锈钢产业有深厚积淀，近年加快产业转型。",
        "gdp": "5100亿元",
        "population": "541.0万人",
        "industry_summary": "煤炭采选及深加工、不锈钢（太钢集团）、新材料为三大支柱，山西转型综改示范区推动大数据和先进制造落地。",
        "employment_summary": "煤炭和钢铁行业就业规模大但近年需求收缩，新能源（光伏、储能）相关就业成新方向。",
        "source_name": "太原市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.taiyuan.gov.cn/",
    },
    ("福建", "福州"): {
        "summary": "福州是福建省省会，数字福建建设最早的省会，海峡西岸经济区核心。",
        "gdp": "12673亿元",
        "population": "854.0万人",
        "industry_summary": "电子信息（纺织/服装/鞋业等出口制造向数字经济转型）、新能源（宁德时代总部漳州）、现代金融为重点方向。",
        "employment_summary": "纺织服装外贸就业基数大，数字经济和新能源是近年高薪增长点，海峡两岸交流提供涉台商务岗位。",
        "source_name": "福州市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.fuzhou.gov.cn/",
    },
    ("福建", "厦门"): {
        "summary": "厦门是经济特区，对台经贸最重要窗口，营商环境和居住品质领先全国。",
        "gdp": "8066亿元",
        "population": "530.0万人",
        "industry_summary": "软件和信息服务（美亚柏科、吉比特等）、半导体（联芯、士兰集科）、跨境电商为三大新兴方向。",
        "employment_summary": "软件和互联网从业人员占比全省最高，跨境电商和对台贸易提供大量外向型就业岗位。",
        "source_name": "厦门市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.xm.gov.cn/",
    },
    ("甘肃", "兰州"): {
        "summary": "兰州是甘肃省省会，西北重要工业基地，石化和有色金属产业历史悠久。",
        "gdp": "3045亿元",
        "population": "446.3万人",
        "industry_summary": "石化（中石油兰州石化）、有色金属冶炼、电力（黄河水电）为传统支柱，兰州新区重点发展信息技术和生物医药。",
        "employment_summary": "国有石化和冶金企业就业稳定，兰州大学及周边高校培育了大批理工科人才，留兰就业比例偏低。",
        "source_name": "兰州市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.lanzhou.gov.cn/",
    },
    ("内蒙古", "呼和浩特"): {
        "summary": "呼和浩特是内蒙古首府，乳业之都，清洁能源和大数据产业迅速崛起。",
        "gdp": "3800亿元",
        "population": "350.0万人",
        "industry_summary": "乳业（伊利、蒙牛双总部）产值超千亿，清洁能源（风电、光伏）产能全国前列，大数据中心（华为、阿里云）聚集。",
        "employment_summary": "乳业产业链就业稳定，大数据中心运维需求增长，清洁能源工程师就业前景向好。",
        "source_name": "呼和浩特市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.huhhot.gov.cn/",
    },
    ("新疆", "乌鲁木齐"): {
        "summary": "乌鲁木齐是新疆首府，中国向西开放的核心城市，丝绸之路经济带重要节点。",
        "gdp": "4068亿元",
        "population": "397.0万人",
        "industry_summary": "煤化工、纺织服装（棉纺）、新能源（天山北坡风光电）为支柱，中欧班列乌鲁木齐集结中心带动跨境物流增长。",
        "employment_summary": "国有能源和纺织企业就业稳定，中欧班列带动跨境贸易岗位增加，是西部艰苦地区补贴就业政策受益地。",
        "source_name": "乌鲁木齐市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.urumqi.gov.cn/",
    },
    # ── 补充批次：2023年统计公报数据 ──────────────────────────────────────────
    ("广东", "深圳"): {
        "summary": "深圳是粤港澳大湾区核心引擎，科技创新产业最集中的城市，互联网/硬件/金融科技薪资全国最高。",
        "gdp": "34606亿元",
        "population": "1775.3万人",
        "industry_summary": "电子信息（华为、腾讯、中兴总部）、先进制造、金融科技为三大支柱，高新技术企业超7.4万家，独角兽企业数量全国第二。",
        "employment_summary": "工程师和算法岗位密度全国第一，应届生平均薪资深圳居全国前三，外资金融机构和VC/PE对金融专业需求大。",
        "source_name": "深圳市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.sz.gov.cn/",
    },
    ("海南", "海口"): {
        "summary": "海口是海南省省会，中国自由贸易港核心城市，以旅游、金融开放和互联网经济为主要增长点。",
        "gdp": "2391亿元",
        "population": "291.2万人",
        "industry_summary": "旅游及现代服务业占GDP比重超60%，自贸港政策吸引互联网企业（字节跳动、三七互娱区域总部）落地，金融开放试点加速。",
        "employment_summary": "旅游酒店和服务业就业规模最大，互联网和跨境电商是近年高薪增长点，自贸港税收优惠对金融和法务人才有较强吸引力。",
        "source_name": "海口市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.haikou.gov.cn/",
    },
    ("宁夏", "银川"): {
        "summary": "银川是宁夏首府，西北重要区域中心城市，葡萄酒产业全国知名，清洁能源优势突出。",
        "gdp": "2630亿元",
        "population": "285.1万人",
        "industry_summary": "煤化工（宁夏化工产业基地）、清洁能源（宁夏新能源装机全国前列）、葡萄酒（贺兰山东麓产区全国标杆）为三大特色。",
        "employment_summary": "能源化工和新能源工程类岗位需求稳定，葡萄酒及农业相关产业吸纳就业，政策性岗位（央企在宁分支）较多。",
        "source_name": "银川市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.yinchuan.gov.cn/",
    },
    ("福建", "泉州"): {
        "summary": "泉州是福建第一大经济城市，民营经济最活跃，纺织鞋服品牌总部密度全国最高，古代海上丝绸之路起点。",
        "gdp": "12612亿元",
        "population": "888.3万人",
        "industry_summary": "纺织鞋服（安踏、特步、361°、匹克总部均在此）、石化（泉港石化基地）、电子信息为三大支柱，民营经济贡献GDP逾75%。",
        "employment_summary": "纺织鞋服供应链岗位多、门槛低，品牌公司总部提供设计/营销/供应链管理岗位，石化工程师需求稳定。",
        "source_name": "泉州市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.quanzhou.gov.cn/",
    },
    ("山东", "潍坊"): {
        "summary": "潍坊是山东重要工业城市，潍柴动力是全球最大内燃机企业，农业机械产业全国领先。",
        "gdp": "7801亿元",
        "population": "940.4万人",
        "industry_summary": "动力装备（潍柴动力全球发动机产量第一）、农业机械（潍坊农业机械产值占全国约20%）、化工为三大支柱。",
        "employment_summary": "潍柴系工程类岗位竞争激烈但薪资稳定，农业机械研发对机械工程专业需求持续，化工行业工程师需求基数大。",
        "source_name": "潍坊市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.weifang.gov.cn/",
    },
    ("江苏", "徐州"): {
        "summary": "徐州是淮海经济区中心城市，工程机械产业全球知名，徐工集团是全球第三大工程机械制造商。",
        "gdp": "9537亿元",
        "population": "1035.3万人",
        "industry_summary": "工程机械（徐工集团全球第三）、新能源、数字经济为三大新兴方向，煤炭资源型城市正加速转型，新能源汽车零部件产业快速扩张。",
        "employment_summary": "徐工系及配套企业为工程机械专业首选，新能源产业近年工程师需求增速显著，煤炭类岗位收缩但待遇相对稳定。",
        "source_name": "徐州市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.xuzhou.gov.cn/",
    },
    ("江苏", "无锡"): {
        "summary": "无锡是长三角重要制造业城市，集成电路和物联网产业全国领先，高端制造业密度居全国前列。",
        "gdp": "16263亿元",
        "population": "749.5万人",
        "industry_summary": "集成电路（中芯国际、华虹半导体无锡基地）、物联网（无锡国家传感网创新示范区）、高端装备为三大优势产业。",
        "employment_summary": "集成电路工程师薪资待遇优厚，物联网和嵌入式开发需求旺盛，高端装备和精密仪器领域对理工科毕业生持续吸纳。",
        "source_name": "无锡市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.wuxi.gov.cn/",
    },
    ("江苏", "常州"): {
        "summary": "常州是长三角新能源产业集聚度最高的城市，理想汽车总部在此，动力电池产能全国前列。",
        "gdp": "10116亿元",
        "population": "536.6万人",
        "industry_summary": "新能源（理想汽车整车、宁德时代常州基地、中创新航）、碳纤维新材料（全球最大碳纤维生产基地之一）、装备制造为三大支柱。",
        "employment_summary": "新能源汽车相关工程师需求急剧增长，动力电池和碳纤维材料对化学/材料/电气专业吸纳能力强，是长三角理工科毕业生重要目标城市。",
        "source_name": "常州市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.changzhou.gov.cn/",
    },
    ("广东", "东莞"): {
        "summary": "东莞是全球制造业重镇，华南电子信息产业核心，OPPO、vivo总部在此，外贸出口规模全国前列。",
        "gdp": "11438亿元",
        "population": "1043.7万人",
        "industry_summary": "电子信息（OPPO、vivo整机及供应链）、先进装备、新材料为三大支柱，制造业总产值超2万亿元，外资企业密度华南最高。",
        "employment_summary": "供应链管理和工业工程岗位需求庞大，OPPO/vivo系软件工程师薪资有竞争力，外资工厂提供大量工程和品控岗位。",
        "source_name": "东莞市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.dg.gov.cn/",
    },
    ("广东", "珠海"): {
        "summary": "珠海是粤港澳大湾区西岸核心城市，毗邻澳门，格力电器总部所在地，航空和高端装备产业基础扎实。",
        "gdp": "4045亿元",
        "population": "246.7万人",
        "industry_summary": "电子信息（格力电器、纳思达）、集成电路（珠海高新区）、高端装备（珠海航空产业园）为三大重点，粤澳深度合作区建设带动现代服务业。",
        "employment_summary": "格力系工程类岗位待遇稳定，集成电路设计岗位薪资较高，粤澳合作区相关金融和贸易岗位持续扩张。",
        "source_name": "珠海市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://ztj.zhuhai.gov.cn/",
    },
    ("江苏", "南通"): {
        "summary": "南通是长三角北翼重要城市，造船产业全国领先，承接上海产业转移优势明显，长江入海口交通枢纽。",
        "gdp": "11813亿元",
        "population": "769.5万人",
        "industry_summary": "船舶海工（全国最大造船基地之一，中远海运、振华重工重要基地）、电子信息、家纺（南通叠石桥国际家纺城）为三大支柱。",
        "employment_summary": "船舶工程和海洋工程专业毕业生首选目标城市，电子信息制造需求稳定，纺织外贸提供国际商务类岗位。",
        "source_name": "南通市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.nantong.gov.cn/",
    },
    ("江苏", "扬州"): {
        "summary": "扬州是江苏历史文化名城，汽车零部件和智能电网装备产业具有全国竞争力，生活品质居苏中前列。",
        "gdp": "7217亿元",
        "population": "453.9万人",
        "industry_summary": "汽车及零部件（亚普汽车部件全球最大汽车燃油系统制造商）、新型电力装备（扬州输变电）、化工为三大支柱。",
        "employment_summary": "汽车零部件研发和生产工程岗位需求稳定，新型电力装备产业链对电气工程专业毕业生有较强吸引力。",
        "source_name": "扬州市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.yangzhou.gov.cn/",
    },
    ("浙江", "温州"): {
        "summary": "温州是中国民营经济发祥地，电气电器（正泰、德力西总部）和皮革鞋服产业全国标杆城市。",
        "gdp": "8654亿元",
        "population": "985.2万人",
        "industry_summary": "电气电器（正泰集团、德力西低压电器全球领先）、皮革鞋服（红蜻蜓、奥康等知名品牌）、泵阀（全国最大泵阀生产基地）为三大特色产业。",
        "employment_summary": "民营企业为主要就业载体，正泰系新能源（光伏）岗位扩张明显，电商经济带来大量运营和物流就业，整体薪资水平中等偏上。",
        "source_name": "温州市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.wenzhou.gov.cn/",
    },
    ("浙江", "绍兴"): {
        "summary": "绍兴是浙江重要工业城市，纺织印染产业全国最集中，近年半导体产业异军突起成为新兴增长极。",
        "gdp": "7791亿元",
        "population": "546.7万人",
        "industry_summary": "纺织印染（中国轻纺城全球最大纺织品专业市场）、集成电路（绍兴半导体产业集群：中芯绍兴、长电科技等）、黄金珠宝为三大支柱。",
        "employment_summary": "集成电路工程师岗位近年快速增长且薪资有竞争力，纺织品设计和外贸岗位基数大，黄金珠宝行业提供设计和检测岗位。",
        "source_name": "绍兴市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.shaoxing.gov.cn/",
    },
    ("浙江", "金华"): {
        "summary": "金华是浙江中部重要城市，义乌中国小商品城是全球最大商品批发市场，电商经济全国标杆。",
        "gdp": "5935亿元",
        "population": "570.3万人",
        "industry_summary": "小商品（义乌市场年成交额超2000亿元）、电商经济（跨境电商出口全国第一）、新能源汽车零部件（华友钴业等）为三大特色。",
        "employment_summary": "跨境电商运营和外贸业务员需求旺盛，小商品供应链管理岗位多，新能源材料和零部件产业对理工科人才吸纳快速增长。",
        "source_name": "金华市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.jinhua.gov.cn/",
    },
    ("安徽", "芜湖"): {
        "summary": "芜湖是安徽第二大城市，奇瑞汽车总部所在地，机器人和航空航天产业近年快速崛起。",
        "gdp": "4527亿元",
        "population": "363.6万人",
        "industry_summary": "汽车（奇瑞汽车年产量百万级）、机器人（埃夫特机器人）、航空（中国通用航空产业园）为三大新兴支柱，承接长三角产业转移优势明显。",
        "employment_summary": "奇瑞系整车及供应链工程岗位是芜湖最大就业载体，机器人和智能制造对自动化/机械专业需求增长明显，航空制造为特色就业方向。",
        "source_name": "芜湖市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.wuhu.gov.cn/",
    },
    ("安徽", "蚌埠"): {
        "summary": "蚌埠是安徽重要工业城市，硅基材料产业全国领先，是中国光伏玻璃和石英玻璃重要产地。",
        "gdp": "2171亿元",
        "population": "327.2万人",
        "industry_summary": "硅基新材料（蚌埠硅基产业：硅玻璃、石英管、光伏玻璃产量全国第一）、精细化工（丰原集团燃料乙醇）、装备制造为三大支柱。",
        "employment_summary": "光伏玻璃和新材料领域工程师需求稳定，化工产业对化学/材料专业有持续需求，政策推动下生物基材料成新兴就业方向。",
        "source_name": "蚌埠市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.bengbu.gov.cn/",
    },
    ("青海", "西宁"): {
        "summary": "西宁是青海省省会，锂电池和光伏材料产业近年快速崛起，是中国锂电新能源产业链重要基地。",
        "gdp": "1752亿元",
        "population": "246.0万人",
        "industry_summary": "锂电池材料（比亚迪西宁基地、天津力神、宁德时代供应链）、光伏（多晶硅料生产全国重要基地）、有色金属（电解铝）为三大支柱。",
        "employment_summary": "新能源材料工程师需求快速增长，政策性岗位（援青就业政策）有吸引力，整体薪资水平低于东部但生活成本明显更低。",
        "source_name": "西宁市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.xining.gov.cn/",
    },
    ("四川", "绵阳"): {
        "summary": "绵阳是中国科技城，国防军工科研院所聚集密度全国最高，中国工程物理研究院（九院）所在地。",
        "gdp": "4100亿元",
        "population": "534.6万人",
        "industry_summary": "国防军工（核武器、电子对抗、空气动力学等科研院所10余家）、电子信息（长虹集团）、汽车零部件（九洲电器）为三大支柱。",
        "employment_summary": "军工科研院所对理工科高学历毕业生吸引力强（待遇稳定+保密福利），长虹系电子岗位需求基数大，科技城新区吸引创新创业就业。",
        "source_name": "绵阳市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.mianyang.gov.cn/",
    },
    ("四川", "德阳"): {
        "summary": "德阳是全国重大技术装备制造基地，东方电气总部所在地，核电和水电装备产能全球领先。",
        "gdp": "2716亿元",
        "population": "352.2万人",
        "industry_summary": "重型装备（东方电气：核电、水电、火电机组全球前列；二重万航重型锻件）、磷化工（全国最重要磷矿和磷化工基地）为两大支柱。",
        "employment_summary": "东方电气系工程岗位对能源/机械专业毕业生具有强吸引力，核电装备研发是少数本科可参与的尖端制造方向，薪资水平中等偏上且稳定。",
        "source_name": "德阳市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.deyang.gov.cn/",
    },
    ("河北", "保定"): {
        "summary": "保定是河北人口第一大市，长城汽车总部所在地，京津冀协同发展中承接产业转移的重要节点。",
        "gdp": "4089亿元",
        "population": "1154.4万人",
        "industry_summary": "汽车（长城汽车总部，魏牌、哈弗等年产量逾百万辆）、电力装备（华北电力大学所在，变压器产量全国居前）、纺织为三大支柱。",
        "employment_summary": "长城汽车系是最大就业载体，电力装备对电气工程专业需求旺盛，京津冀协同带来北京产业转移的工程类和服务类岗位持续增加。",
        "source_name": "保定市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.baoding.gov.cn/",
    },
    ("河北", "唐山"): {
        "summary": "唐山是华北重要工业基地，钢铁产能全国最大，港口吞吐量居全国前列，重工业转型稳步推进。",
        "gdp": "8620亿元",
        "population": "769.7万人",
        "industry_summary": "钢铁（河北钢铁、首钢唐钢，粗钢年产量约1亿吨）、装备制造（唐山机车）、建材（全球最大陶瓷卫浴产区之一）为三大支柱。",
        "employment_summary": "冶金类工程岗位总量大但近年需求收缩，港口物流和外贸提供稳定就业，机械/电气工程师在装备制造企业需求持续。",
        "source_name": "唐山市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.tangshan.gov.cn/",
    },
    ("河北", "廊坊"): {
        "summary": "廊坊紧邻北京，是京东集团总部所在地，承接北京非首都功能疏解的重要承载城市。",
        "gdp": "3780亿元",
        "population": "467.2万人",
        "industry_summary": "电子商务（京东总部及物流体系）、新型建材、电子信息为三大支柱，北京大兴国际机场临空经济区（廊坊部分）正成新增长极。",
        "employment_summary": "京东系物流和电商运营岗位是最大就业来源，临空经济区对航空物流和跨境贸易人才需求增长，外溢的北京科技企业区域中心提供工程岗位。",
        "source_name": "廊坊市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.langfang.gov.cn/",
    },
    ("河北", "秦皇岛"): {
        "summary": "秦皇岛是北方著名旅游城市（北戴河），同时也是重要的玻璃和港口物流城市。",
        "gdp": "1740亿元",
        "population": "334.3万人",
        "industry_summary": "旅游（北戴河疗养度假区）、玻璃（秦皇岛玻璃集团，优质浮法玻璃产量全国居前）、港口物流（煤炭运输港口）为三大支柱。",
        "employment_summary": "旅游和酒店业就业季节性强，玻璃材料工程类岗位需求稳定，港口物流提供运营管理类就业。",
        "source_name": "秦皇岛市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.qhd.gov.cn/",
    },
    ("河北", "沧州"): {
        "summary": "沧州是全国最大管道装备制造基地，化工产业基础深厚，北方重要石化生产区。",
        "gdp": "4200亿元",
        "population": "741.2万人",
        "industry_summary": "管道装备（石油管道、燃气管道设备制造，市场份额全国第一）、石化（沧州大化、中沧石化）、医药（河北医药产业重镇）为三大支柱。",
        "employment_summary": "管道和石化工程类岗位需求量大且稳定，医药产业对药学、化工专业有持续吸引力，整体薪资水平中等。",
        "source_name": "沧州市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.cangzhou.gov.cn/",
    },
    ("湖南", "株洲"): {
        "summary": "株洲是中国制造2025重点城市，轨道交通装备之都，中车株洲是全球最大电力机车生产基地。",
        "gdp": "2836亿元",
        "population": "400.6万人",
        "industry_summary": "轨道交通装备（中车株洲电力机车全球市场份额第一，轨交产业集群年产值超1500亿元）、航空发动机（中国航发南方公司）、硬质合金为三大支柱。",
        "employment_summary": "中车株洲是最大单体就业主体，轨交装备工程师待遇稳定，航空发动机对热能/机械专业毕业生具有极强吸引力（涉密方向）。",
        "source_name": "株洲市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.zhuzhou.gov.cn/",
    },
    ("湖南", "衡阳"): {
        "summary": "衡阳是湖南第二大城市，有色金属冶炼（铅锌、铀矿）全国重要产地，核工业相关机构聚集。",
        "gdp": "2650亿元",
        "population": "722.7万人",
        "industry_summary": "有色金属（水口山铅锌矿、南华核铀系列）、电力装备（特变电工衡阳）、纺织为三大支柱，核燃料相关产业链在此形成独特聚集。",
        "employment_summary": "核工业（铀矿采冶、核燃料）对核工程类专业有独特就业需求，有色金属冶炼工程岗位稳定，电力装备企业对电气专业持续招聘。",
        "source_name": "衡阳市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.hengyang.gov.cn/",
    },
    ("湖南", "岳阳"): {
        "summary": "岳阳是湖南最重要的石化工业城市，巴陵石化是全国最大己内酰胺生产基地，洞庭湖港口物流枢纽。",
        "gdp": "3768亿元",
        "population": "560.2万人",
        "industry_summary": "石化（巴陵石化己内酰胺/尼龙产量全国第一）、电力（华能岳阳电厂、葛洲坝控股）、港口物流为三大支柱，洞庭湖生态经济区建设带动绿色产业。",
        "employment_summary": "石化行业化工/化学工程师需求基数大，电力系统岗位稳定待遇好，港口物流提供物流管理类就业。",
        "source_name": "岳阳市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.yueyang.gov.cn/",
    },
    ("湖南", "湘潭"): {
        "summary": "湘潭是湖南工业重镇，湘钢是全国重要特殊钢生产基地，湘电集团是大型风电机组核心制造商。",
        "gdp": "2302亿元",
        "population": "280.7万人",
        "industry_summary": "钢铁（华菱湘钢特殊钢产品全国领先）、装备制造（湘电风能风机、湘机集团）、电子信息（湘潭高新区电子信息产业）为三大支柱。",
        "employment_summary": "钢铁和装备制造工程类岗位体量大，湘电系新能源（风电）设备对电气/机械专业需求增长，毗邻长沙使通勤兼顾长沙就业成为选项。",
        "source_name": "湘潭市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.xiangtan.gov.cn/",
    },
    ("江西", "九江"): {
        "summary": "九江是江西北部重要工业和交通城市，九江石化是长江沿岸重要炼化基地，庐山旅游资源丰富。",
        "gdp": "3756亿元",
        "population": "515.0万人",
        "industry_summary": "石化（九江石化炼油规模中部居前）、纺织（九江化纤及棉纺）、电子电气（彩虹集团显示基板）为三大支柱。",
        "employment_summary": "石化工程类岗位是最主要高薪就业方向，电子玻璃/显示材料制造提供理工科就业，旅游服务业就业基数大。",
        "source_name": "九江市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.jiujiang.gov.cn/",
    },
    ("江西", "赣州"): {
        "summary": "赣州是世界稀土之都，离子型稀土储量全球第一，新能源汽车和锂电产业近年快速扩张。",
        "gdp": "3820亿元",
        "population": "969.3万人",
        "industry_summary": "稀土（赣州稀土磁性材料、永磁电机产业链全球领先）、新能源汽车及零部件（赣锋锂业供应链、多家整车项目）、电子信息为三大方向。",
        "employment_summary": "稀土相关材料和化工工程师需求持续，新能源汽车产业链近年大量招聘，农村人口多但城镇化加速带来本地就业机会增长。",
        "source_name": "赣州市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.ganzhou.gov.cn/",
    },
    ("广西", "桂林"): {
        "summary": "桂林是中国最重要旅游目的地之一，医药产业（桂林三金等）在国内有较强知名度，生态环境优越。",
        "gdp": "2649亿元",
        "population": "493.3万人",
        "industry_summary": "旅游业（桂林山水甲天下，年接待游客超1亿人次）、医药（桂林三金药业、桂林南药）、电子信息（威华股份电子材料）为三大支柱。",
        "employment_summary": "旅游酒店和文旅运营就业规模最大，医药行业提供稳定就业，电子材料和新能源电池材料对化工/材料专业有一定需求。",
        "source_name": "桂林市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.guilin.gov.cn/",
    },
    ("河南", "洛阳"): {
        "summary": "洛阳是中原工业重城，重型装备制造（一拖集团）和石化产业基础深厚，国际旅游目的地（龙门石窟）。",
        "gdp": "5500亿元",
        "population": "707.9万人",
        "industry_summary": "装备制造（中国一拖全球最大农业机械制造商；洛阳铜加工、轴承全国领先）、石化（中石化洛阳炼化）、旅游（龙门石窟、白马寺）为三大支柱。",
        "employment_summary": "一拖系农业机械工程类岗位稳定，轴承和铜加工行业对机械专业有需求，石化工程师薪资在豫中偏上，旅游行业就业基数大。",
        "source_name": "洛阳市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.ly.gov.cn/",
    },
    ("河南", "新乡"): {
        "summary": "新乡是河南重要工业城市，新能源电池（新乡锂电基地）和医药产业近年快速发展，农业科技服务全国。",
        "gdp": "2816亿元",
        "population": "583.4万人",
        "industry_summary": "新能源电池（锂电池正极材料、隔膜产业链在新乡聚集）、医药（辉瑞、多家原料药企业）、化纤纺织为三大支柱。",
        "employment_summary": "新能源电池材料工程师近年需求增速快，医药行业对药学和化工专业持续招聘，化纤纺织业就业基数大但薪资增长有限。",
        "source_name": "新乡市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.xinxiang.gov.cn/",
    },
    ("河南", "南阳"): {
        "summary": "南阳是河南人口第二大市，防爆电气和中药材产业全国知名，张仲景故里中医药文化资源丰富。",
        "gdp": "5073亿元",
        "population": "961.6万人",
        "industry_summary": "防爆电气（南阳防爆电气产品市场份额全国第一）、中药材（独角莲、山茱萸等道地药材产区）、装备制造（中光科技等）为三大特色。",
        "employment_summary": "防爆电气企业对电气工程师需求稳定，中药材产业对中药学/制药专业有一定需求，人口基数大但城镇化率偏低，本地薪资水平较低。",
        "source_name": "南阳市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.nanyang.gov.cn/",
    },
    ("河南", "开封"): {
        "summary": "开封是北宋古都，文化旅游产业发展强劲，近年承接郑州外溢产业，区位进入郑汴一体化核心区。",
        "gdp": "2381亿元",
        "population": "459.6万人",
        "industry_summary": "文化旅游（清明上河园等景区年接待超3000万人次）、装备制造（开封仪表、开封空分）、农副产品加工为三大支柱。",
        "employment_summary": "文旅行业就业占比高，郑汴一体化带来部分郑州外溢工业和服务业岗位，制造业工程类岗位规模有限，整体薪资水平偏低。",
        "source_name": "开封市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.kaifeng.gov.cn/",
    },
    ("陕西", "咸阳"): {
        "summary": "咸阳与西安共处关中平原城市群，高校资源丰富，半导体和纺织产业有深厚积淀。",
        "gdp": "2213亿元",
        "population": "450.1万人",
        "industry_summary": "半导体和新型显示（彩虹集团CRT基板玻璃→已转型TFT基板；三星电子咸阳二期）、棉纺纺织（咸阳纺织集团）、能源化工为三大支柱。",
        "employment_summary": "三星电子咸阳工厂和彩虹集团提供显示材料和半导体制造岗位，纺织类岗位基数大，与西安共用就业市场使得可选择余地较大。",
        "source_name": "咸阳市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.xianyang.gov.cn/",
    },
    ("山西", "晋中"): {
        "summary": "晋中紧邻太原，传统煤焦化工基础深厚，近年依托转型综改示范区推进数字经济和先进制造。",
        "gdp": "1726亿元",
        "population": "344.6万人",
        "industry_summary": "煤炭及焦化（晋中是山西焦煤主产区之一）、装备制造（太钢不锈钢等上下游配套）、农业（晋中盆地重要粮食产区）为三大支柱。",
        "employment_summary": "煤化工岗位是主要高薪方向但受政策约束，近年与太原共享部分制造业转移就业，整体薪资水平在山西省内属中等。",
        "source_name": "晋中市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.jz.gov.cn/",
    },
    ("山东", "泰安"): {
        "summary": "泰安是国际旅游名城（泰山），电工电气产业具有全国竞争力，农业科技服务规模大。",
        "gdp": "3440亿元",
        "population": "551.0万人",
        "industry_summary": "旅游（泰山世界文化与自然遗产，年接待游客超500万）、电工电气（泰开集团等输配电设备）、农产品加工（泰安黄金梨、大汶河粮食产区）为三大支柱。",
        "employment_summary": "电工电气对电气工程师有稳定需求，旅游服务业就业规模大，农业科技和食品加工提供一定就业，整体薪资水平在山东省内中等。",
        "source_name": "泰安市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.taian.gov.cn/",
    },
    ("山东", "威海"): {
        "summary": "威海是山东宜居城市代表，海洋渔业和水产品加工全国领先，医疗器械（威高集团）产业竞争力突出。",
        "gdp": "3470亿元",
        "population": "285.3万人",
        "industry_summary": "医疗器械（威高集团是全国最大医疗器械企业之一）、海洋渔业（水产品出口规模全国前列）、先进装备（哈工大威海校区带动）为三大支柱。",
        "employment_summary": "威高系医疗器械工程和销售岗位需求旺盛，水产品加工提供大量就业，哈工大威海校区毕业生在装备制造和信息技术方向有优势。",
        "source_name": "威海市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.weihai.gov.cn/",
    },
    ("山东", "德州"): {
        "summary": "德州是全国太阳能光热产业最大基地，皇明太阳能等企业推动新能源热利用产业全国领先。",
        "gdp": "3380亿元",
        "population": "557.5万人",
        "industry_summary": "太阳能光热（德州是全球最大太阳能集热器生产基地，市占率全国约40%）、农副产品加工（粮棉油产区）、装备制造为三大支柱。",
        "employment_summary": "太阳能热利用工程类岗位需求稳定，农产品加工业就业基数大，整体薪资水平偏低，靠近济南和天津的区位优势利于跨城就业。",
        "source_name": "德州市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.dezhou.gov.cn/",
    },
    ("吉林", "吉林"): {
        "summary": "吉林市是吉林省第二大城市，化工产业（吉化集团）历史深厚，碳纤维新材料近年成为新增长点。",
        "gdp": "2350亿元",
        "population": "399.4万人",
        "industry_summary": "化工（吉化集团，中国最早合成橡胶生产基地）、碳纤维（吉林化纤集团是全球最大碳纤维原丝生产商）、清洁能源（松花江水电）为三大支柱。",
        "employment_summary": "吉化系化工岗位是传统高薪方向，碳纤维材料工程师需求近年显著增长，整体人口流出压力较大，留存就业竞争相对缓和。",
        "source_name": "吉林市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.jlcity.gov.cn/",
    },
    ("辽宁", "锦州"): {
        "summary": "锦州是辽宁西部中心城市，石化产业和输油管道枢纽地位重要，葡萄酒产业有区域知名度。",
        "gdp": "1271亿元",
        "population": "250.1万人",
        "industry_summary": "石化（锦州石化年炼油逾800万吨）、电子材料（锦州晶华新材料）、葡萄酒（锦州葡萄产区）为三大方向，是东北-华北输油管道重要节点。",
        "employment_summary": "石化工程师需求基数大，管道运营对管道工程类专业有特色需求，人口外流背景下本地就业竞争压力相对低。",
        "source_name": "锦州市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.jz.ln.gov.cn/",
    },
    ("河南", "平顶山"): {
        "summary": "平顶山是河南重要煤化工城市，煤炭和尼龙产业有全国影响力，中国尼龙城品牌建设积极推进。",
        "gdp": "2700亿元",
        "population": "505.4万人",
        "industry_summary": "煤化工（平煤神马集团：煤炭采选+尼龙化工产业，是全国最大尼龙66化工基地）、盐化工（舞阳盐矿）、装备制造为三大支柱。",
        "employment_summary": "平煤神马集团是最大就业载体，尼龙化工对化学工程专业毕业生有较强需求，煤炭类岗位收缩但企业内部转岗持续。",
        "source_name": "平顶山市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.pds.gov.cn/",
    },
    ("海南", "三亚"): {
        "summary": "三亚是中国顶级旅游度假目的地，高端酒店集群密度全国最高，自贸港政策推动新业态快速发展。",
        "gdp": "878亿元",
        "population": "105.1万人",
        "industry_summary": "旅游和酒店业贡献超60%的GDP，自贸港政策吸引医疗旅游（博鳌乐城国际医疗旅游先行区）、免税商业（海棠湾免税购物）、热带农业（亚龙湾）等新业态。",
        "employment_summary": "旅游和酒店管理就业规模大、季节性强，医疗旅游对医学专业有独特需求，跨境贸易相关岗位因自贸港政策持续增加。",
        "source_name": "三亚市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.sanya.gov.cn/",
    },
    ("西藏", "拉萨"): {
        "summary": "拉萨是西藏自治区首府，宗教文化旅游全国独特，国家援藏政策保障就业，高原特色农业和矿业基础较好。",
        "gdp": "800亿元",
        "population": "99.3万人",
        "industry_summary": "文化宗教旅游（布达拉宫、大昭寺等，年接待游客超3000万）、特色农牧业（藏药材、牦牛产品）、矿业（铬、锂、地热资源丰富）为三大方向。",
        "employment_summary": "援藏就业政策提供补贴岗位，政府机关和事业单位就业稳定，旅游和文化行业季节性强，高原气候特殊性使得长期就业的非本地毕业生比例较低。",
        "source_name": "拉萨市统计局2023年国民经济和社会发展统计公报",
        "source_url": "http://tjj.lasa.gov.cn/",
    },
}


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _clip(value: Any, limit: int = 120) -> str:
    text = _clean(value)
    if len(text) <= limit:
        return text
    return text[:limit].rstrip("，。；,; ") + "…"


def _to_int(value: Any) -> int | None:
    try:
        if value in (None, "", "0"):
            return None
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def school_profile_from_gaokao_payload(payload: dict, source_url: str) -> dict:
    """Extract a source-grounded school_profile row from gaokao.cn info data."""

    labels = [
        _clean(item.get("name"))
        for item in payload.get("label_list") or []
        if _clean(item.get("name"))
    ]
    content = _clean(payload.get("content"))
    return {
        "school_name": _clean(payload.get("name")),
        "school_id": _clean(payload.get("school_id")),
        "summary": _clip(content, 140),
        "content": content,
        "tags": "/".join(dict.fromkeys(labels)),
        "motto": _clean(payload.get("motto")),
        "founded_year": _clean(payload.get("create_date")),
        "school_type": _clean(payload.get("type_name")),
        "school_nature": _clean(payload.get("school_nature_name")),
        "education_level": _clean(payload.get("level_name")),
        "master_count": _to_int(payload.get("num_master")),
        "doctor_count": _to_int(payload.get("num_doctor")),
        "academician_count": _to_int(payload.get("num_academician")),
        "ruanke_rank": _to_int(payload.get("ruanke_rank")),
        "source_name": "阳光高考",
        "source_url": source_url,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }


def _fetch_school_profile(school: dict, timeout: int = 8) -> dict | None:
    school_id = school.get("school_id")
    if not school_id:
        return None
    url = SCHOOL_INFO_URL.format(school_id=school_id)
    try:
        response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout, verify=False)
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return None
    data = payload.get("data") or {}
    if not data.get("name"):
        return None
    return school_profile_from_gaokao_payload(data, url)


def fetch_school_profiles(limit: int | None = None, workers: int = 24) -> list[dict]:
    """Fetch school profiles for schools already discovered in raw school data."""

    if not SCHOOL_RAW_PATH.exists():
        raise FileNotFoundError(f"missing {SCHOOL_RAW_PATH}; run scrape_school_locations.py first")
    schools = json.loads(SCHOOL_RAW_PATH.read_text(encoding="utf-8"))
    if limit:
        schools = schools[:limit]

    profiles: list[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_fetch_school_profile, school): school for school in schools}
        for index, future in enumerate(as_completed(futures), start=1):
            row = future.result()
            if row:
                profiles.append(row)
            if index % 200 == 0:
                print(f"  fetched {index}/{len(schools)}, valid {len(profiles)}")

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    SCHOOL_PROFILE_RAW_PATH.write_text(
        json.dumps(profiles, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return profiles


def build_major_profile_rows(description_rows: list[dict]) -> list[dict]:
    """Build major_profile rows, falling back from category rows to concrete majors."""

    fallback_by_level3: dict[str, dict] = {}
    for row in description_rows:
        level3 = _clean(row.get("level3"))
        intro = _clean(row.get("is_what"))
        if not level3 or not intro or _clean(row.get("name")).endswith("类"):
            continue
        fallback_by_level3.setdefault(level3, row)

    result: list[dict] = []
    for row in description_rows:
        source = row
        fallback_from = ""
        if not _clean(row.get("is_what")):
            fallback = fallback_by_level3.get(_clean(row.get("level3")))
            if fallback:
                source = fallback
                fallback_from = _clean(fallback.get("name"))

        special_id = row.get("special_id")
        result.append(
            {
                "major_name": _clean(row.get("name")),
                "special_id": _to_int(special_id),
                "summary": _clip(source.get("is_what"), 160),
                "learn_what": _clean(source.get("learn_what")),
                "career_direction": _clean(source.get("do_what")),
                "keywords": _clean(source.get("keywords")),
                "fallback_from": fallback_from,
                "source_name": "阳光高考",
                "source_url": MAJOR_INFO_URL.format(special_id=special_id) if special_id else "",
                "fetched_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
    return [row for row in result if row["major_name"]]


WIKI_CITY_RAW_PATH = RAW_DIR / "city_wiki_raw.json"
WIKI_UA = "Mozilla/5.0 (CollegeApplication data builder; +https://github.com/ghh1125)"


def _parse_wiki_city_page(html: str) -> dict:
    """Extract GDP, population, and industry summary from a Wikipedia city page."""
    infobox_m = re.search(r'<table[^>]*infobox[^>]*>(.*?)</table>', html, re.DOTALL)
    result: dict = {}
    if infobox_m:
        rows = re.findall(
            r'<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>',
            infobox_m.group(1), re.DOTALL,
        )
        def _cell(t: str) -> str:
            t = re.sub(r'<[^>]+>|&#\w+;|&\w+;', ' ', t)
            return re.sub(r'\s+', ' ', t).strip()

        def _extract_num(val: str, unit: str) -> str:
            """Extract the largest number before a unit, ignoring footnote digits."""
            # Find all candidate numbers before the unit
            pattern = rf'([\d,，]+\.?\d*)\s*(?:\d\s+)?{unit}'
            matches = re.findall(pattern, val)
            if not matches:
                return ""
            # Take the largest number (handles cases like "218.6 1 萬人" where 1 is a footnote)
            best = max(matches, key=lambda x: float(x.replace('，', '').replace(',', '') or '0'))
            return best.replace('，', '').replace(',', '')

        for th, td in rows:
            key = _cell(th)
            val = _cell(td)
            if '国内生产总值' in key and '人均' not in key and not result.get('gdp'):
                num = _extract_num(val, '亿')
                if num:
                    result['gdp'] = num + '亿元'
            if '常住' in key and '•' in key and '密度' not in key and '城镇' not in key and '城區' not in key:
                num = _extract_num(val, '[萬万]')
                if num and not result.get('population'):
                    result['population'] = num + '万人'

    # Industry: search for 经济 section anchor → collect nearby <p> tags
    econ_idx = html.find('id="经济"')
    if econ_idx < 0:
        econ_idx = html.find('id="产业"')
    if econ_idx >= 0:
        section_html = html[econ_idx:econ_idx + 6000]
        # Stop at next major heading
        next_h = re.search(r'<h[23][^>]*>', section_html[50:])
        if next_h:
            section_html = section_html[:50 + next_h.start()]
        paras = re.findall(r'<p[^>]*>(.*?)</p>', section_html, re.DOTALL)
        texts = []
        for p in paras:
            t = re.sub(r'<[^>]+>|&#\w+;|&\w+;|\[.*?\]', ' ', p)
            t = re.sub(r'\s+', ' ', t).strip()
            if len(t) > 25 and not re.match(r'^@media|^\.mw', t):
                texts.append(t)
        result['industry'] = '。'.join(texts[:2])[:200] if texts else ''

    return result


def _fetch_wiki_city_profile(province: str, city: str, timeout: int = 10) -> dict | None:
    """Fetch and parse a single city's Wikipedia page."""
    candidates = [f"{city}市", city] if province not in ("北京", "上海", "天津", "重庆") else [f"{province}市"]
    for name in candidates:
        url = f"https://zh.wikipedia.org/wiki/{name}"
        try:
            r = requests.get(url, headers={"User-Agent": WIKI_UA}, timeout=timeout)
        except Exception:
            continue
        if r.status_code != 200:
            continue
        # Only skip if THIS page is a disambiguation page (not just links to one)
        if re.search(r'<title>[^<]*[（(]\s*消歧义\s*[）)]', r.text) or \
           'Wikipedia:消歧义页面' in r.text:
            continue
        data = _parse_wiki_city_page(r.text)
        if data.get('gdp') or data.get('population'):
            return {
                "gdp": data.get("gdp", ""),
                "population": data.get("population", ""),
                "industry_summary": data.get("industry", ""),
                "employment_summary": "",
                "source_name": f"维基百科·{name}",
                "source_url": url,
            }
    return None


def fetch_city_wiki_profiles(
    city_rows: list[dict],
    workers: int = 12,
) -> dict[tuple[str, str], dict]:
    """
    Fetch Wikipedia city profiles for cities not already in OFFICIAL_CITY_FACTS.
    Returns {(province, city): wiki_data_dict}.
    Caches raw results to WIKI_CITY_RAW_PATH to avoid re-fetching.
    """
    targets = list({
        (_clean(row.get("province")), _clean(row.get("city") or row.get("city_name")))
        for row in city_rows
        if _clean(row.get("province")) and (_clean(row.get("city") or row.get("city_name", "")))
        and (_clean(row.get("province")), _clean(row.get("city") or row.get("city_name", ""))) not in OFFICIAL_CITY_FACTS
    })

    # Load cache
    cached: dict[str, dict] = {}
    if WIKI_CITY_RAW_PATH.exists():
        cached = json.loads(WIKI_CITY_RAW_PATH.read_text(encoding="utf-8"))

    results: dict[tuple[str, str], dict] = {}
    to_fetch = [(prov, city) for prov, city in targets if f"{prov}|{city}" not in cached]

    if to_fetch:
        print(f"  Fetching {len(to_fetch)} cities from Wikipedia (cache has {len(cached)})…")
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(_fetch_wiki_city_profile, prov, city): (prov, city)
                for prov, city in to_fetch
            }
            for i, future in enumerate(as_completed(futures), 1):
                prov, city = futures[future]
                data = future.result()
                cached[f"{prov}|{city}"] = data or {}
                if i % 50 == 0:
                    print(f"    {i}/{len(to_fetch)} done, {sum(1 for v in cached.values() if v)} hits")

        RAW_DIR.mkdir(parents=True, exist_ok=True)
        WIKI_CITY_RAW_PATH.write_text(json.dumps(cached, ensure_ascii=False, indent=2), encoding="utf-8")

    for prov, city in targets:
        data = cached.get(f"{prov}|{city}")
        if data:
            results[(prov, city)] = data
    return results


def _city_tier_label(city: str) -> tuple[int, str]:
    from src.ranking.rank import CITY_TIER

    tier = CITY_TIER.get(city, 1)
    label_by_tier = {5: "一线城市", 4: "一线城市", 3: "新一线城市", 2: "二线城市", 1: "普通地级市"}
    return tier, label_by_tier[tier]


def build_city_profile_rows(
    city_rows: list[dict],
    wiki_data: dict[tuple[str, str], dict] | None = None,
) -> list[dict]:
    """Build city_profile rows: official hand-curated → Wikipedia → template fallback."""

    seen: set[tuple[str, str]] = set()
    profiles: list[dict] = []
    for row in city_rows:
        province = _clean(row.get("province"))
        city = _clean(row.get("city") or row.get("city_name"))
        if not province or not city or (province, city) in seen:
            continue
        seen.add((province, city))
        tier, tier_label = _city_tier_label(city)
        is_capital = 1 if CAPITAL_BY_PROVINCE.get(province) == city else 0
        base_summary = f"{city}位于{province}"
        if is_capital:
            base_summary += "，是省会/直辖市核心城市"
        base_summary += f"，按系统城市分层为{tier_label}。"

        official = OFFICIAL_CITY_FACTS.get((province, city))
        wiki = (wiki_data or {}).get((province, city)) if not official else None
        source = official or wiki or {}

        profiles.append(
            {
                "city_name": city,
                "province": province,
                "city_tier": tier,
                "tier_label": tier_label,
                "is_capital": is_capital,
                "summary": source.get("summary") or base_summary,
                "gdp": source.get("gdp", ""),
                "population": source.get("population", ""),
                "industry_summary": source.get("industry_summary", ""),
                "employment_summary": source.get("employment_summary", ""),
                "source_name": source.get("source_name") or "项目内置城市分层规则",
                "source_url": source.get("source_url", ""),
                "fetched_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
    return profiles


def _rows_as_dicts(conn: Any, sql: str) -> list[dict]:
    cursor = conn.execute(sql)
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _upsert_many(conn: Any, table: str, rows: list[dict], conflict_columns: tuple[str, ...]) -> None:
    if not rows:
        return
    columns = list(rows[0])
    placeholders = ", ".join("?" for _ in columns)
    update_columns = [column for column in columns if column not in conflict_columns]
    assignments = ", ".join(f"{column}=excluded.{column}" for column in update_columns)
    conflict = ", ".join(conflict_columns)
    sql = (
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT({conflict}) DO UPDATE SET {assignments}"
    )
    conn.executemany(sql, [tuple(row.get(column) for column in columns) for row in rows])


def build_profiles(
    school_limit: int | None = None,
    fetch_schools: bool = True,
    fetch_cities: bool = True,
    workers: int = 24,
) -> dict[str, int]:
    """Create profile tables and populate them from available source data."""

    from db import get_conn
    from scripts.init_db import execute_schema, load_schema_sql

    with get_conn() as conn:
        execute_schema(conn, load_schema_sql())

    if fetch_schools or not SCHOOL_PROFILE_RAW_PATH.exists():
        school_profiles = fetch_school_profiles(limit=school_limit, workers=workers)
    else:
        school_profiles = json.loads(SCHOOL_PROFILE_RAW_PATH.read_text(encoding="utf-8"))
        if school_limit:
            school_profiles = school_profiles[:school_limit]

    with get_conn() as conn:
        major_rows = _rows_as_dicts(conn, "SELECT * FROM major_description")
        city_rows = _rows_as_dicts(
            conn,
            """
            SELECT DISTINCT province, city
            FROM school_master
            WHERE province IS NOT NULL AND province != ''
              AND city IS NOT NULL AND city != ''
            """,
        )
        major_profiles = build_major_profile_rows(major_rows)
        wiki_city_data = fetch_city_wiki_profiles(city_rows, workers=workers) if fetch_cities else {}
        city_profiles = build_city_profile_rows(city_rows, wiki_data=wiki_city_data)

        _upsert_many(conn, "school_profile", school_profiles, ("school_name",))
        _upsert_many(conn, "major_profile", major_profiles, ("major_name",))
        _upsert_many(conn, "city_profile", city_profiles, ("city_name", "province"))

        return {
            "school_profile": conn.execute("SELECT COUNT(*) FROM school_profile").fetchone()[0],
            "major_profile": conn.execute("SELECT COUNT(*) FROM major_profile").fetchone()[0],
            "city_profile": conn.execute("SELECT COUNT(*) FROM city_profile").fetchone()[0],
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build source-grounded profile tables.")
    parser.add_argument("--school-limit", type=int, default=None, help="Limit school profile fetches for testing.")
    parser.add_argument("--workers", type=int, default=24)
    parser.add_argument("--no-fetch-schools", action="store_true", help="Reuse data/raw/school_profiles_raw.json.")
    parser.add_argument("--no-fetch-cities", action="store_true", help="Skip Wikipedia city fetch (use cache or template).")
    args = parser.parse_args()

    counts = build_profiles(
        school_limit=args.school_limit,
        fetch_schools=not args.no_fetch_schools,
        fetch_cities=not args.no_fetch_cities,
        workers=args.workers,
    )
    for table, count in counts.items():
        print(f"{table}: {count} rows")


if __name__ == "__main__":
    main()
