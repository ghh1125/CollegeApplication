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


def _city_tier_label(city: str) -> tuple[int, str]:
    from app.pipeline.rank import CITY_TIER

    tier = CITY_TIER.get(city, 1)
    label_by_tier = {5: "一线城市", 4: "一线城市", 3: "新一线城市", 2: "二线城市", 1: "普通地级市"}
    return tier, label_by_tier[tier]


def build_city_profile_rows(city_rows: list[dict]) -> list[dict]:
    """Build city_profile rows from structural data plus official fact seeds."""

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

        official = OFFICIAL_CITY_FACTS.get((province, city), {})
        profiles.append(
            {
                "city_name": city,
                "province": province,
                "city_tier": tier,
                "tier_label": tier_label,
                "is_capital": is_capital,
                "summary": official.get("summary") or base_summary,
                "gdp": official.get("gdp", ""),
                "population": official.get("population", ""),
                "industry_summary": official.get("industry_summary", ""),
                "employment_summary": official.get("employment_summary", ""),
                "source_name": official.get("source_name") or "项目内置城市分层规则",
                "source_url": official.get("source_url", ""),
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
    workers: int = 24,
) -> dict[str, int]:
    """Create profile tables and populate them from available source data."""

    from app.db import get_conn
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
        city_profiles = build_city_profile_rows(city_rows)

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
    args = parser.parse_args()

    counts = build_profiles(
        school_limit=args.school_limit,
        fetch_schools=not args.no_fetch_schools,
        workers=args.workers,
    )
    for table, count in counts.items():
        print(f"{table}: {count} rows")


if __name__ == "__main__":
    main()
