"""保形替换器（给 AI 读的"换名版"）。

核心不是"随便编个值"，而是**编得像**：类型一致、格式一致、量级一致、
关系一致。同一个实体在全文档、多轮里必须映射到同一个假值，所以所有生成都
先查会话实体图（``ctx.session``），没有才生成并登记。

会话图兼容三种形状：

1. ``pipeline.Session``（``map`` / ``is_used`` / ``mark_used``）——正式路径；
2. 普通 dict（``{"PERSON": {"张伟": "陈志远"}}``）——测试/脚本路径；
3. ``None``——退回进程级兜底表，保证同一进程内仍然确定。

覆盖类型：人名、机构、地址、手机/固话、身份证、银行卡/账号、金额、
案号、执业证号、统一社会信用代码、邮箱、车牌、经纬度、图幅号、
坐标系名、控制点号、病历号/客户号/流水号、IBAN、密钥，其余走通用兜底
（同长度随机字符，保留数字/字母/汉字类别比例）。

实现要点：
- 生成随机数用 ``md5(seed|etype|value)`` 播种，因此同一会话里同一个原值
  一定生成同一个假值，与调用顺序无关。
- 生成后通过 ``is_used`` 查重，避免两个不同原值映射到同一个假值。
- 身份证重新算 GB 11643 校验位、银行卡重新算 Luhn、IBAN 重新算 mod 97、
  统一社会信用代码重新算 GB 32100 校验位——保证成品校验合法。
"""
from __future__ import annotations

import hashlib
import random
import re
import threading

from ..registry import register_replacer
from ..udm import Decision, ReplaceContext, Rule

# ══════════════════════════════ 词典 ══════════════════════════════

_FAKE_SURNAMES = (
    "陈林黄赵吴周徐孙马朱胡郭何高罗郑梁谢宋唐许韩冯邓曹彭曾肖田董袁潘"
    "于蒋蔡余杜叶程苏魏吕丁任沈姚卢姜崔钟谭陆汪范金石廖贾夏韦付方白邹"
    "孟熊秦邱江尹薛闫段雷侯龙史陶黎贺顾毛郝龚邵万钱严覃武戴莫孔向汤"
)
_FAKE_GIVEN = (
    "志远嘉豪子轩浩宇明辉建华俊杰思远雅静雨涵欣然若曦梓涵一诺晨曦梦琪"
    "欣怡语嫣文博学业成航鑫泽宁远卓然知行弘毅景行清和云舒书言益嘉"
    "天佑安然思齐若冰亦凡子墨景天新宇世豪家豪泽宇"
)
_FAKE_COMPOUND = ("欧阳", "司马", "上官", "诸葛", "皇甫", "公孙", "慕容")
_LATIN_FIRST = ("James", "Michael", "David", "John", "Robert", "William",
                "Mary", "Linda", "Susan", "Karen", "Emily", "Daniel", "Henry")
_LATIN_LAST = ("Smith", "Miller", "Brown", "Wilson", "Taylor", "Anderson",
               "Clark", "Lewis", "Walker", "Hall", "Young", "Green", "Baker")
_ASCII_NAME_RE = re.compile(r"[A-Za-z][A-Za-z .'\-]*")

_ORG_CORES = (
    "衡平", "恒正", "致远", "博远", "宏图", "嘉禾", "金诚", "华信", "中瑞",
    "正大", "联合", "东方", "远航", "鼎盛", "天成", "瑞丰", "嘉信", "安泰",
    "卓远", "立信", "永信", "德胜", "新华", "长虹", "明理", "天衡", "广合",
    "众成", "康达", "同泽", "恒信", "鼎业", "国正", "景行", "弘毅", "嘉元",
    "泰安", "元贞", "永泰", "万方", "千帆", "百川", "长风", "云程", "星汉",
    "青禾", "沧海", "远山", "白鹭", "知行", "慎思", "明辨", "笃行",
)
_ORG_CORE_CHARS = "华信诚达恒泰安瑞丰源通盛和正德永嘉景弘元长云星海天"
_ORG_SUFFIXES = tuple(sorted({
    "股份有限公司", "有限责任公司", "有限公司", "分公司", "子公司",
    "律师事务所", "会计师事务所", "税务师事务所", "专利事务所", "事务所",
    "人民检察院", "人民法院", "检察院", "法院",
    "研究院", "研究所", "设计院", "科学院",
    "银行", "支行", "分行", "信用社",
    "大学", "学院", "学校", "中学", "小学", "幼儿园",
    "医院", "卫生院", "妇幼保健院", "疾控中心", "诊所",
    "集团", "控股", "公司", "委员会", "管理中心", "中心", "基金会", "协会",
    "商会", "联合会", "工作室", "办事处", "管理局", "监督局", "税务局",
}, key=len, reverse=True))
_INDUSTRY_WORDS = tuple(sorted({
    "科技", "商贸", "实业", "建设", "工程", "投资", "管理", "咨询", "贸易",
    "电子", "信息", "网络", "医药", "生物", "地产", "置业", "物流", "文化",
    "传媒", "控股", "发展", "国际", "资产", "财务", "教育", "医疗", "健康",
    "汽车", "机械", "化工", "能源", "环保", "材料", "食品", "农业", "旅游",
    "酒店", "餐饮", "服装", "家居", "银行", "保险", "证券", "基金", "信托",
    "租赁", "担保", "技术", "服务", "设计", "装饰", "建筑", "物业", "商业",
    "矿业", "钢铁", "纺织", "器械", "智能", "数据", "云计算", "人民",
    "高级", "中级", "基层",
}, key=len, reverse=True))

_LOCALITY_NAMES = (
    "北京", "上海", "天津", "重庆", "河北", "山西", "辽宁", "吉林",
    "黑龙江", "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南",
    "湖北", "湖南", "广东", "海南", "四川", "贵州", "云南", "陕西",
    "甘肃", "青海", "台湾", "内蒙古", "广西", "西藏", "宁夏", "新疆",
    "香港", "澳门", "中国", "中华", "杭州", "南京", "苏州", "无锡",
    "常州", "宁波", "温州", "绍兴", "嘉兴", "合肥", "福州", "厦门",
    "泉州", "南昌", "济南", "青岛", "烟台", "郑州", "洛阳", "武汉",
    "宜昌", "长沙", "株洲", "广州", "深圳", "珠海", "佛山", "东莞",
    "中山", "惠州", "成都", "绵阳", "昆明", "大理", "西安", "咸阳",
    "兰州", "太原", "大同", "沈阳", "大连", "鞍山", "长春", "哈尔滨",
    "大庆", "石家庄", "唐山", "保定", "呼和浩特", "包头", "南宁", "桂林",
    "海口", "三亚", "贵阳", "遵义", "拉萨", "银川", "西宁", "乌鲁木齐",
)
_LOCALITY_NAMES_SORTED = tuple(sorted(_LOCALITY_NAMES, key=len, reverse=True))
_DISTRICT_NAMES = (
    "朝阳", "海淀", "西城", "东城", "丰台", "石景山", "通州", "昌平", "顺义",
    "大兴", "房山", "门头沟", "怀柔", "平谷", "密云", "延庆", "徐汇", "静安",
    "黄浦", "长宁", "普陀", "虹口", "杨浦", "闵行", "宝山", "嘉定", "浦东",
    "松江", "青浦", "奉贤", "金山", "崇明", "西湖", "上城", "拱墅", "滨江",
    "萧山", "余杭", "临平", "钱塘", "富阳", "临安", "越城", "柯桥", "上虞",
    "吴兴", "南浔", "秀洲", "南湖", "玄武", "秦淮", "建邺", "鼓楼", "浦口",
    "栖霞", "雨花台", "江宁", "六合", "溧水", "高淳", "锦江", "青羊", "金牛",
    "武侯", "成华", "龙泉驿", "温江", "新都", "双流", "越秀", "海珠", "荔湾",
    "天河", "白云", "番禺", "花都", "南沙", "从化", "增城", "福田", "罗湖",
    "南山", "宝安", "龙岗", "盐田", "龙华", "坪山", "光明",
)
_ROAD_NAMES = (
    "永定", "长安", "新华", "建设", "人民", "解放", "中山", "文化", "光明",
    "和平", "友谊", "胜利", "东风", "朝阳", "复兴", "通达", "锦绣", "望江",
    "春晖", "玉兰", "梧桐", "香樟", "银杏", "望春", "安宁", "同福", "万寿",
    "广济", "惠安", "嘉善", "民安", "文昌", "文景", "兴国", "兴华", "永康",
)
_AREAS_3 = ("010", "021", "022", "023", "024", "025", "027", "028", "029")
_AREAS_4 = ("0311", "0351", "0371", "0431", "0451", "0531", "0532", "0571",
            "0574", "0591", "0592", "0731", "0755", "0769", "0771", "0871",
            "0898", "0991")
_EMAIL_DOMAINS = ("163.com", "126.com", "qq.com", "outlook.com", "gmail.com",
                  "foxmail.com", "sina.com", "sohu.com")
_CRS_POOL = ("WGS84", "CGCS2000", "北京54", "西安80", "UTM Zone 50N",
             "EPSG:4326", "EPSG:4490", "本地独立坐标系")
_PLATE_PROVINCES = "京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼"
_CANONICAL = {
    "PERSON": "PERSON", "NAME": "PERSON", "PARTY": "PERSON", "PATIENT": "PERSON",
    "DOCTOR": "PERSON", "LAWYER": "PERSON", "CONTACT": "PERSON", "CUSTOMER": "PERSON",
    "ORG": "ORG", "COMPANY": "ORG", "HOSPITAL": "ORG", "INSTITUTION": "ORG",
    "COURT": "ORG", "SCHOOL": "ORG", "AGENT_ORG": "ORG",
    "ADDRESS": "ADDRESS", "LOCATION": "ADDRESS", "PLACE": "ADDRESS", "ADDR": "ADDRESS",
    "PHONE": "PHONE", "TEL": "PHONE", "MOBILE": "PHONE", "LANDLINE": "PHONE",
    "ID_CARD": "ID_CARD", "IDCARD": "ID_CARD", "ID_NUMBER": "ID_CARD", "ID": "ID_CARD",
    "BANK": "BANK", "BANK_CARD": "BANK", "CARD": "BANK",
    "ACCOUNT": "ACCOUNT", "BANK_ACCOUNT": "ACCOUNT",
    "AMOUNT": "AMOUNT", "MONEY": "AMOUNT", "CREDIT_LINE": "AMOUNT",
    "BALANCE": "AMOUNT", "PRICE": "AMOUNT",
    "CASE_NO": "CASE_NO", "CASE_NUMBER": "CASE_NO",
    "LICENSE": "LICENSE", "CERT_NO": "LICENSE", "PRACTICE_NO": "LICENSE",
    "USCC": "USCC", "CREDIT_CODE": "USCC", "BIZ_LICENSE": "USCC",
    "EMAIL": "EMAIL", "MAIL": "EMAIL",
    "PLATE": "PLATE", "CAR_PLATE": "PLATE", "LICENSE_PLATE": "PLATE",
    "GEO_COORD": "GEO_COORD", "COORD": "GEO_COORD", "COORDINATE": "GEO_COORD",
    "SHEET_NO": "SHEET_NO", "MAP_SHEET": "SHEET_NO",
    "CRS": "CRS", "COORD_SYSTEM": "CRS",
    "CONTROL_POINT": "CONTROL_POINT", "POINT_NO": "CONTROL_POINT",
    "MEDICAL_RECORD": "RECORD_NO", "CUSTOMER_NO": "RECORD_NO",
    "TXN_NO": "RECORD_NO", "RECORD_NO": "RECORD_NO", "ORDER_NO": "RECORD_NO",
    "IBAN": "IBAN",
    "SECRET": "SECRET", "PRIVATE_KEY": "SECRET", "PASSWORD": "SECRET",
    "DEPT": "DEPT", "DEPARTMENT": "DEPT", "DIAGNOSIS": "DIAGNOSIS",
}

# ══════════════════════════════ 会话适配 ══════════════════════════════

_GLOBAL_MAP: dict[str, dict[str, str]] = {}
_GLOBAL_USED: dict[str, set[str]] = {}
_GLOBAL_LOCK = threading.RLock()


def _lookup(ctx: ReplaceContext, etype: str, value: str) -> str | None:
    """查会话实体图里已有的假值。"""
    session = ctx.session
    if session is None:
        with _GLOBAL_LOCK:
            return _GLOBAL_MAP.get(etype, {}).get(value)
    if isinstance(session, dict):
        nested = session.get(etype)
        if isinstance(nested, dict):
            got = nested.get(value)
            return got if isinstance(got, str) else None
        flat = session.get(f"{etype}:{value}")
        return flat if isinstance(flat, str) else None
    flat_map = getattr(session, "map", None)
    if isinstance(flat_map, dict):
        got = flat_map.get((etype, value))
        return got if isinstance(got, str) else None
    for attr in ("graph", "mapping", "data", "entities"):
        data = getattr(session, attr, None)
        if isinstance(data, dict) and isinstance(data.get(etype), dict):
            got = data[etype].get(value)
            return got if isinstance(got, str) else None
    return None


def _remember(ctx: ReplaceContext, etype: str, value: str, repl: str) -> None:
    """把 原值 -> 假值 写回会话实体图。"""
    session = ctx.session
    if session is None:
        with _GLOBAL_LOCK:
            _GLOBAL_MAP.setdefault(etype, {})[value] = repl
        return
    if isinstance(session, dict):
        current = session.get(etype)
        if isinstance(current, dict) or current is None:
            session.setdefault(etype, {})[value] = repl
        else:                                          # 扁平键形状
            session[f"{etype}:{value}"] = repl
        return
    flat_map = getattr(session, "map", None)
    if isinstance(flat_map, dict):
        flat_map[(etype, value)] = repl
        return
    for attr in ("graph", "mapping", "data", "entities"):
        data = getattr(session, attr, None)
        if isinstance(data, dict):
            data.setdefault(etype, {})[value] = repl
            return
    with _GLOBAL_LOCK:                                 # 实在写不进去，退回兜底
        _GLOBAL_MAP.setdefault(etype, {})[value] = repl


def _is_used(ctx: ReplaceContext, etype: str, repl: str) -> bool:
    """该假值是否已被同类型其它原值占用（避免撞车）。"""
    session = ctx.session
    if session is None:
        with _GLOBAL_LOCK:
            return repl in _GLOBAL_USED.get(etype, set())
    checker = getattr(session, "is_used", None)
    if callable(checker):
        try:
            return bool(checker(etype, repl))
        except Exception:                              # noqa: BLE001 - 会话对象形状不可控
            pass
    if isinstance(session, dict):
        nested = session.get(etype)
        if isinstance(nested, dict):
            return repl in nested.values()
        return any(key.startswith(f"{etype}:") and val == repl
                   for key, val in session.items())
    flat_map = getattr(session, "map", None)
    if isinstance(flat_map, dict):
        return repl in flat_map.values()
    with _GLOBAL_LOCK:
        return repl in _GLOBAL_USED.get(etype, set())


def _mark_used(ctx: ReplaceContext, etype: str, repl: str) -> None:
    """登记已用假值（pipeline.Session 自带 mark_used）。"""
    session = ctx.session
    if session is None:
        with _GLOBAL_LOCK:
            _GLOBAL_USED.setdefault(etype, set()).add(repl)
        return
    marker = getattr(session, "mark_used", None)
    if callable(marker):
        try:
            marker(etype, repl)
            return
        except Exception:                              # noqa: BLE001
            pass
    if isinstance(session, dict):
        nested = session.get(etype)
        if not isinstance(nested, dict):
            with _GLOBAL_LOCK:
                _GLOBAL_USED.setdefault(etype, set()).add(repl)
    # dict 会话的占用信息可从 nested.values() 推导，不必单独记


# ══════════════════════════════ 通用工具 ══════════════════════════════

_DIGITS = "0123456789"
_ALNUM = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_LOWER_ALNUM = "abcdefghijklmnopqrstuvwxyz0123456789"
_CN_POOL = "的一是了我不人在他有这个上们来到时大地为子中你说生国年着就那和要她出也得里后自以会家可下而过天去能对小多然于心学么之都好看起发当没成只如事把还用第样道想作种开美总从无情己面最女但现前些所同日手又行意动方期它头经长儿回位分爱老因很给名法间斯知世什两次使身者被高已亲其进此话常与活正感"
_LABEL_PREFIX_RE = re.compile(r"^([^\dA-Za-z]{1,16}?[：:]\s*)(.+)$", re.S)
# 不带冒号的标签也保留（"住院号ZY2023001"、"账号6222…"）
_KNOWN_LABELS = (
    "交易流水号", "银行账号", "银行帐号", "客户编号", "客户代码", "授信额度",
    "授信金额", "信用额度", "贷款金额", "贷款余额", "账户余额", "交易金额",
    "转账金额", "交易编号", "病历号", "病案号", "住院号", "门诊号", "就诊号",
    "医保号", "床位号", "检查号", "检验号", "报告号", "客户号", "客户ID",
    "客户账号", "客户帐号", "会员号", "流水号", "凭证号", "订单号", "回单号",
    "参考号", "交易序号", "控制点号", "控制点", "水准点", "导线点", "点号",
    "GNSS点", "GPS点", "账号", "帐号", "账户号", "账户", "帐户", "卡号",
    "床号", "余额", "金额",
)
_KNOWN_LABEL_RE = re.compile(
    r"^(?P<label>(?:" + "|".join(
        re.escape(w) for w in sorted(_KNOWN_LABELS, key=len, reverse=True)
    ) + r")\s*[:：]?\s*)(?P<rest>[0-9A-Za-z¥￥].*)$", re.S)
_ID_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_ID_CHECK = "10X98765432"
_USCC_CHARSET = "0123456789ABCDEFGHJKLMNPQRTUWXY"
_USCC_WEIGHTS = (1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28)
_USCC_INDEX = {ch: i for i, ch in enumerate(_USCC_CHARSET)}
_IBAN_CHARS = {chr(ord("A") + i): str(10 + i) for i in range(26)}


def _is_chinese(ch: str) -> bool:
    return "\u4e00" <= ch <= "\u9fff"


def _digits(rng: random.Random, n: int) -> str:
    return "".join(rng.choice(_DIGITS) for _ in range(max(0, n)))


def _nonzero_digits(rng: random.Random, n: int) -> str:
    if n <= 0:
        return ""
    return rng.choice("123456789") + _digits(rng, n - 1)


def _split_label(value: str) -> tuple[str, str]:
    """拆出"联系电话："这类标签前缀，标签原样保留。"""
    match = _LABEL_PREFIX_RE.match(value)
    if match:
        return match.group(1), match.group(2)
    known = _KNOWN_LABEL_RE.match(value)
    if known:
        return known.group("label"), known.group("rest")
    return "", value


def _reinject(template: str, digits: str) -> str:
    """把新数字按原模板的数字位置回填（保留空格/短横线分组）。"""
    out: list[str] = []
    idx = 0
    for ch in template:
        if ch.isdigit():
            out.append(digits[idx] if idx < len(digits) else ch)
            idx += 1
        else:
            out.append(ch)
    if idx < len(digits):
        out.append(digits[idx:])
    return "".join(out)


def _remap(template: str, new: str) -> str:
    """把新串按原模板的字母数字位置回填（保留总分隔符）。"""
    out: list[str] = []
    idx = 0
    for ch in template:
        if ch.isalnum():
            out.append(new[idx] if idx < len(new) else ch)
            idx += 1
        else:
            out.append(ch)
    if idx < len(new):
        out.append(new[idx:])
    return "".join(out)


def _rng_for(ctx: ReplaceContext, etype: str, value: str) -> random.Random:
    """确定性随机源：同 seed/同类型/同原值 -> 同一条随机序列。"""
    seed = f"{getattr(ctx, 'seed', 0)}|{etype}|{value}"
    digest = hashlib.md5(seed.encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _mod97(text: str) -> int:
    remainder = 0
    for ch in text:
        chunk = _IBAN_CHARS.get(ch, ch)
        for digit in chunk:
            remainder = (remainder * 10 + (ord(digit) - 48)) % 97
    return remainder


def _id_check_digit(body17: str) -> str:
    total = sum(int(ch) * w for ch, w in zip(body17[:17], _ID_WEIGHTS))
    return _ID_CHECK[total % 11]


def _uscc_check_digit(body17: str) -> str:
    total = sum(_USCC_INDEX.get(ch, 0) * w for ch, w in zip(body17[:17], _USCC_WEIGHTS))
    return _USCC_CHARSET[(31 - total % 31) % 31]


def _luhn_check_digit(partial: str) -> str:
    total = 0
    for idx, ch in enumerate(reversed(partial)):
        n = ord(ch) - 48
        if idx % 2 == 0:                               # 校验位在末位，partial 末位是双倍位
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return str((10 - total % 10) % 10)


def _fake_generic(value: str, rng: random.Random) -> str:
    """通用兜底：同长度随机字符，保留数字/字母/汉字类别比例。"""
    out: list[str] = []
    for ch in value:
        if ch.isdigit():
            out.append(rng.choice(_DIGITS))
        elif "a" <= ch <= "z":
            out.append(rng.choice("abcdefghijklmnopqrstuvwxyz"))
        elif "A" <= ch <= "Z":
            out.append(rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))
        elif _is_chinese(ch):
            out.append(rng.choice(_CN_POOL))
        else:
            out.append(ch)
    result = "".join(out)
    if result == value and value:                      # 至少变一个字符
        chars = list(result)
        for idx, ch in enumerate(chars):
            if ch.isdigit():
                chars[idx] = "0" if ch != "0" else "1"
                return "".join(chars)
            if _is_chinese(ch):
                chars[idx] = "某" if ch != "某" else "甲"
                return "".join(chars)
        return value + "*"
    return result


# ══════════════════════════════ 各类型生成器 ══════════════════════════════

def _fake_person(value: str, rng: random.Random, attempt: int = 0) -> str:
    """人名：中文名用姓池+名池、长度贴近原名；"某"式匿名指代保留结构。"""
    body = value
    if body.endswith("某"):
        count = len(body)
        return rng.choice(_FAKE_SURNAMES) + "某" * max(1, count - 1)
    if _ASCII_NAME_RE.fullmatch(body):
        first = rng.choice(_LATIN_FIRST)
        last = rng.choice(_LATIN_LAST)
        if " " in body.strip():
            return f"{first} {last}"
        return first
    n = len(body)
    if n <= 1:
        return rng.choice(_FAKE_SURNAMES)
    target = n if 2 <= n <= 4 else 2
    if target >= 4 and rng.random() < 0.3:
        surname = rng.choice(_FAKE_COMPOUND)
        given = "".join(rng.choice(_FAKE_GIVEN) for _ in range(target - 2))
    else:
        surname = rng.choice(_FAKE_SURNAMES)
        given = "".join(rng.choice(_FAKE_GIVEN) for _ in range(max(1, target - 1)))
    return surname + given


def _split_locality(stem: str) -> tuple[str, str]:
    """从机构/地址名里切出"省市区"前缀，返回 (locality, rest)。"""
    pos = 0
    for name in _LOCALITY_NAMES_SORTED:
        if stem.startswith(name):
            pos = len(name)
            break
    if pos == 0:
        match = re.match(r"^[\u4e00-\u9fa5]{2,10}?(?:自治区|特别行政区|省|市|地区|盟)", stem)
        if match:
            pos = match.end()
    for token in ("自治区", "特别行政区", "省", "市", "地区", "盟", "区", "县",
                  "旗", "镇", "乡", "街道"):
        if stem.startswith(token, pos):
            pos += len(token)
            break
    if pos:
        match = re.match(r"^[\u4e00-\u9fa5]{1,8}?(?:市|区|县|旗|镇|乡|街道)",
                         stem[pos:])
        if match:
            pos += match.end()
    return stem[:pos], stem[pos:]


def _replace_district(locality: str, rng: random.Random) -> str:
    """机构名没有字号可用时，换一个同长度的区/县/城市名（同城感）。"""
    match = re.search(r"([\u4e00-\u9fa5]{2,4})(?:区|县|市)$", locality)
    if match:
        body = match.group(1)
        pool = [d for d in _DISTRICT_NAMES if len(d) == len(body)] or list(_DISTRICT_NAMES)
        return locality[:match.start(1)] + rng.choice(pool) + locality[match.end(1):]
    for name in _LOCALITY_NAMES_SORTED:
        if locality.startswith(name):
            pool = [c for c in _LOCALITY_NAMES_SORTED if len(c) == len(name)]
            return rng.choice(pool or list(_LOCALITY_NAMES_SORTED)) + locality[len(name):]
    return locality


def _org_core(rng: random.Random, target: int) -> str:
    """生成 1–4 字的机构字号。"""
    target = max(1, min(4, target))
    core = rng.choice(_ORG_CORES)
    if target <= len(core):
        return core[:target]
    return core + "".join(rng.choice(_ORG_CORE_CHARS) for _ in range(target - len(core)))


def _strip_industry(rest: str) -> tuple[str, str]:
    """剥掉机构名末尾的行业词（科技/商贸/人民…），返回 (core, industry)。"""
    for word in _INDUSTRY_WORDS:
        if rest == word:
            return "", word
        if rest.endswith(word):
            return rest[:-len(word)], word
    return rest, ""


def _fake_org(value: str, rng: random.Random, attempt: int = 0) -> str:
    """机构：保留结尾类型词（律师事务所/医院/公司），字号换掉；保留地区前缀。"""
    label, body = _split_label(value)
    annotation = ""
    match = re.search(r"[（(][^（）()]{1,12}[）)]$", body)
    if match:
        annotation = match.group(0)
        body = body[:match.start()].strip()

    suffix = ""
    for candidate in _ORG_SUFFIXES:
        if body.endswith(candidate) and len(body) > len(candidate):
            suffix = candidate
            break
    if not suffix:
        return label + _fake_generic(body, rng) + annotation

    stem = body[:-len(suffix)]
    locality, rest = _split_locality(stem)
    core, industry = _strip_industry(rest)

    if not core:
        if industry:                                   # 只有行业词，插入字号
            core_len = 2
        elif locality:
            # 法院/检察院这类没有字号的机构：换掉最后的区/县级名称
            new_locality = _replace_district(locality, rng) if locality else locality
            return label + new_locality + industry + suffix + annotation
        else:
            core_len = 2
    else:
        core_len = len(core)

    new_core = _org_core(rng, core_len)
    return label + locality + new_core + industry + suffix + annotation


_LOCALITY_HEAD_RE = re.compile(
    r"^[\u4e00-\u9fa5]{2,10}?(?:自治区|特别行政区|省|市|地区|盟)")


def _fake_address(value: str, rng: random.Random, attempt: int = 0) -> str:
    """地址：保留省市区层级（同城），后面换成随机路名 + 门牌。

    注意：若整段地址本身就只有“省/市/区”层级、切不出 street 部分
    （``rest`` 为空），保留前缀就等于把完整原值写进假值 → 必须替换地区名，
    否则会构成真实泄漏（R04 回归用例）。
    """
    label, body = _split_label(value)
    locality, rest = _split_locality(body)
    if locality and not rest.strip():
        locality = _replace_district(locality, rng)
    has = {token: token in rest for token in ("村", "弄", "号", "栋", "楼", "单元", "室")}
    road = rng.choice(_ROAD_NAMES)
    if has["村"]:
        parts = [road + "村"]
    else:
        road_suffix = "街" if ("街" in rest and "路" not in rest) else "路"
        parts = [road + road_suffix]
    if has["弄"]:
        parts.append(f"{rng.randint(1, 999)}弄")
    if has["号"] or (not has["弄"] and not has["村"]):
        parts.append(f"{rng.randint(1, 999)}号")
    if has["栋"]:
        parts.append(f"{rng.randint(1, 30)}栋")
    if has["楼"]:
        parts.append(f"{rng.randint(1, 30)}楼")
    if has["单元"]:
        parts.append(f"{rng.randint(1, 6)}单元")
    if has["室"]:
        parts.append(f"{rng.randint(101, 2801)}室")
    return label + locality + "".join(parts)


def _pick_area(rng: random.Random, digits: int) -> tuple[str, int]:
    """按号码总位数挑一个真实号段（保证区号+本地号长度对得上）。"""
    options: list[tuple[str, int]] = []
    for area in _AREAS_3:
        if 7 <= digits - len(area) <= 8:
            options.append((area, digits - len(area)))
    for area in _AREAS_4:
        if 7 <= digits - len(area) <= 8:
            options.append((area, digits - len(area)))
    if not options:
        return "", digits
    return rng.choice(options)


def _fake_phone(value: str, rng: random.Random, attempt: int = 0) -> str:
    """电话：生成格式合法的新号码（手机 1[3-9] 开头 11 位；固话保留号段风格）。"""
    label, body = _split_label(value)
    digits = re.sub(r"\D", "", body)
    count = len(digits)
    if count == 11 and digits.startswith("1"):
        new = "1" + rng.choice("3456789") + _digits(rng, 9)
    elif count >= 10 and digits.startswith("0"):
        area, local_len = _pick_area(rng, count)
        if not area:
            new = "0" + _digits(rng, count - 1)
        else:
            new = area + _nonzero_digits(rng, local_len)
    else:
        new = _nonzero_digits(rng, max(1, count))
    return label + _reinject(body, new)


def _fake_id(value: str, rng: random.Random, attempt: int = 0) -> str:
    """身份证：同省前 6 位、随机合理生日、随机顺序码、重算 GB 11643 校验位。"""
    label, body = _split_label(value)
    text = body.strip().upper()
    if not re.fullmatch(r"\d{17}[\dX]", text):
        return label + _fake_generic(body, rng)
    region = text[:6]
    year = rng.randint(1950, 2005)
    month = rng.randint(1, 12)
    day = rng.randint(1, 28)
    sequence = rng.randint(0, 999)
    body17 = f"{region}{year:04d}{month:02d}{day:02d}{sequence:03d}"
    return label + body17 + _id_check_digit(body17)


def _fake_bank(value: str, rng: random.Random, attempt: int = 0) -> str:
    """银行卡：同长度、同分组空格，重新算 Luhn 校验位保证合法。"""
    label, body = _split_label(value)
    digits = re.sub(r"\D", "", body)
    total = len(digits)
    if not (12 <= total <= 25):
        return label + _fake_generic(body, rng)
    prefix = digits[:2] if digits[:1] in "23456" else rng.choice("23456")
    partial = prefix + _digits(rng, total - len(prefix) - 1)
    new = partial + _luhn_check_digit(partial)
    return label + _reinject(body, new)


def _fake_account(value: str, rng: random.Random, attempt: int = 0) -> str:
    """账号：同长度同分组的纯数字串；带字母的按类别逐位替换。"""
    label, body = _split_label(value)
    digit_count = sum(1 for ch in body if ch.isdigit())
    if digit_count >= 8 and all(ch.isdigit() or ch in " -" for ch in body):
        return label + _reinject(body, _nonzero_digits(rng, digit_count))
    return label + _fake_code(body, rng)


def _fake_code(body: str, rng: random.Random) -> str:
    """保类别逐位替换（数字->数字、大写->大写、小写->小写）。"""
    out: list[str] = []
    for ch in body:
        if ch.isdigit():
            out.append(rng.choice(_DIGITS))
        elif "a" <= ch <= "z":
            out.append(rng.choice("abcdefghijklmnopqrstuvwxyz"))
        elif "A" <= ch <= "Z":
            out.append(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ"))
        else:
            out.append(ch)
    result = "".join(out)
    return result if result != body else _fake_generic(body, rng)


_AMOUNT_RE = re.compile(
    r"^(?P<pre>[^\d]{0,10}?)(?P<int>\d{1,3}(?:[ ,]\d{3})+|\d+)"
    r"(?P<dec>\.\d+)?(?P<unit>\s*(?:万亿|亿元|万元|亿|万|元|圆|块))?$")
_CN_NUMERALS = "壹贰叁肆伍陆柒捌玖"


def _fake_amount(value: str, rng: random.Random, attempt: int = 0) -> str:
    """金额：±25% 内浮动，保持数量级、小数位、千分位（含空格分隔）和单位。"""
    label, body = _split_label(value)
    match = _AMOUNT_RE.match(body)
    if not match:
        if any(ch in _CN_NUMERALS for ch in body):     # 中文大写金额
            out = "".join(
                rng.choice(_CN_NUMERALS.replace(ch, "")) if ch in _CN_NUMERALS else ch
                for ch in body
            )
            return label + out
        return label + _fake_generic(body, rng)

    pre = match.group("pre")
    int_text = match.group("int")
    dec = match.group("dec") or ""
    unit = match.group("unit") or ""
    # 分隔符：逗号优先，其次空格（判决书/OCR 常把千分位排成空格）
    if "," in int_text:
        separator = ","
    elif " " in int_text:
        separator = " "
    else:
        separator = ""
    plain = int_text.replace(",", "").replace(" ", "")
    original = int(plain)
    digits_len = len(plain)
    lower = max(0.75, (10 ** (digits_len - 1)) / original) if original else 0.75
    upper = min(1.25, ((10 ** digits_len) - 1) / original) if original else 1.25
    if lower > upper:
        lower, upper = 0.75, 1.25
    factor = rng.uniform(lower, upper) if upper > lower else lower
    new_value = int(round(original * factor))
    new_value = max(10 ** (digits_len - 1), min((10 ** digits_len) - 1, new_value))
    if new_value == original:                          # 小数位/相邻数微调，保证变值
        if digits_len > 1:
            new_value = max(10 ** (digits_len - 1),
                            min((10 ** digits_len) - 1,
                                original + rng.choice((-1, 1))))
        else:
            new_value = 1 if original != 1 else 2
    if separator:
        groups: list[str] = []
        text_digits = str(new_value).zfill(digits_len)
        first = len(text_digits) % 3 or 3
        groups.append(text_digits[:first])
        for pos in range(first, len(text_digits), 3):
            groups.append(text_digits[pos:pos + 3])
        new_int = separator.join(groups)
    else:
        new_int = str(new_value).zfill(digits_len)
    if dec:
        decimal_digits = len(dec) - 1
        new_dec = "." + "".join(rng.choice(_DIGITS) for _ in range(decimal_digits))
    else:
        new_dec = ""
    return label + pre + new_int + new_dec + unit


def _fake_case_no(value: str, rng: random.Random, attempt: int = 0) -> str:
    """案号：保留"（年份）法院代字民初"结构，序号换成随机数（保留空格排版）。"""
    label, body = _split_label(value)
    new_serial = str(rng.randint(10000, 99999))
    if "号" in body:
        idx = body.rfind("号")
        # 序号和「号」之间常有排版空格："民再1 号"
        match = re.search(r"(\d+)(\s*)$", body[:idx])
        if match:
            return label + body[:match.start(1)] + new_serial + body[match.start(1) + len(match.group(1)):]
        # 跨行案号末尾可能没有「号」，倒序找最后一段数字
    match = re.search(r"(\d+)(\s*)$", body)
    if match:
        return label + body[:match.start(1)] + new_serial + body[match.end(1):]
    return label + _fake_generic(body, rng)


def _fake_license(value: str, rng: random.Random, attempt: int = 0) -> str:
    """执业证号：同长度；数字号段重排（末位保留 X/数字形态）。"""
    label, body = _split_label(value)
    text = body.strip()
    if re.fullmatch(r"\d{17}[\dXx]", text):
        tail = rng.choice("0123456789X") if text[-1] in "Xx" else rng.choice(_DIGITS)
        return label + _digits(rng, 17) + tail
    if len(text) == 18:
        return label + _fake_uscc_body(text, rng)
    return label + _fake_code(body, rng)


def _fake_uscc_body(text: str, rng: random.Random) -> str:
    """生成 18 位统一社会信用代码：结构合法 + GB 32100 校验位正确。"""
    first = text[:1] if text[:1].isdigit() and text[:1] != "0" else rng.choice("91234")
    second = rng.choice("123456789")
    region = _digits(rng, 6)
    main = "".join(rng.choice(_USCC_CHARSET[10:]) for _ in range(9))
    body17 = first + second + region + main
    return body17 + _uscc_check_digit(body17)


def _fake_uscc(value: str, rng: random.Random, attempt: int = 0) -> str:
    """统一社会信用代码：同长度、结构合法、校验位正确。"""
    label, body = _split_label(value)
    text = body.strip().upper()
    if len(text) == 18:
        return label + _fake_uscc_body(text, rng)
    return label + _fake_code(body, rng)


def _fake_email(value: str, rng: random.Random, attempt: int = 0) -> str:
    """邮箱：本地部分随机、域名换成公共邮箱域，保证格式合法。"""
    label, body = _split_label(value)
    match = re.fullmatch(r"([A-Za-z0-9._%+\-]+)@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})", body)
    if not match:
        return label + _fake_generic(body, rng)
    local = match.group(1)
    length = max(4, min(len(local), 20))
    new_local = rng.choice("abcdefghijklmnopqrstuvwxyz") + "".join(
        rng.choice(_LOWER_ALNUM) for _ in range(length - 1))
    return label + f"{new_local}@{rng.choice(_EMAIL_DOMAINS)}"


def _fake_plate(value: str, rng: random.Random, attempt: int = 0) -> str:
    """车牌：保留省份简称，后面的字母数字整体重排。"""
    label, body = _split_label(value)
    text = body.strip()
    if text and text[0] in _PLATE_PROVINCES and len(text) >= 7:
        letters = "ABCDEFGHJKLMNPQRSTUVWXYZ"
        new = text[0] + rng.choice(letters)
        new += "".join(rng.choice("0123456789ABCDEFGHJKLMNPQRSTUVWXYZ")
                       for _ in range(len(text) - 2))
        if text[-1] in "挂学警港澳":
            new = new[:-1] + text[-1]
        return label + new
    return label + _fake_code(body, rng)


_DMS_RE = re.compile(r"(\d{2,3})[°度]\s*(\d{1,2})['′分]\s*(\d{1,2}(?:\.\d+)?)[\"″秒]?")
_DECIMAL_RE = re.compile(r"-?\d{1,3}\.\d{4,10}")
# 坐标 token：度分秒 或 十进制，按出现顺序逐个平移
_COORD_TOKEN_RE = re.compile(f"(?:{_DMS_RE.pattern})|(?:{_DECIMAL_RE.pattern})")


def _rebuild_dms(match: re.Match[str], rng: random.Random, body: str) -> str:
    """把度分秒坐标平移一点点（保留单位符号与小数位）。"""
    degrees = int(match.group(1))
    minutes = int(match.group(2))
    seconds_text = match.group(3)
    seconds = float(seconds_text)
    decimal_digits = len(seconds_text.split(".")[1]) if "." in seconds_text else 0
    total = degrees * 3600 + minutes * 60 + seconds + rng.uniform(-0.08, 0.08) * 3600
    total = max(0.0, min(180 * 3600.0, total))
    new_degrees = int(total // 3600)
    rest = total - new_degrees * 3600
    new_minutes = int(rest // 60)
    new_seconds = rest - new_minutes * 60
    deg_unit = "°" if "°" in body else "度"
    min_unit = "'" if "'" in body else ("′" if "′" in body else "分")
    if "″" in body:
        sec_unit = "″"
    elif '"' in body:
        sec_unit = '"'
    elif "秒" in body:
        sec_unit = "秒"
    else:
        sec_unit = ""
    if decimal_digits:
        sec_text = f"{new_seconds:.{decimal_digits}f}"
    else:
        sec_text = str(int(new_seconds))
    return f"{new_degrees}{deg_unit}{new_minutes}{min_unit}{sec_text}{sec_unit}"


def _rebuild_decimal(match: re.Match[str], rng: random.Random) -> str:
    """十进制坐标：平移 ±0.05 度，保留小数位数。"""
    text = match.group(0)
    value = float(text)
    decimal_digits = len(text.split(".")[1]) if "." in text else 0
    shifted = value + rng.uniform(-0.05, 0.05)
    shifted = max(-180.0, min(180.0, shifted))
    return f"{shifted:.{decimal_digits}f}"


def _fake_coord(value: str, rng: random.Random, attempt: int = 0) -> str:
    """经纬度：整体小幅度平移（保形假坐标），度分秒与十进制都支持。"""
    label, body = _split_label(value)
    pieces: list[str] = []
    position = 0
    for match in _COORD_TOKEN_RE.finditer(body):
        pieces.append(body[position:match.start()])
        token = match.group(0)
        if "°" in token or "度" in token:
            dms_match = _DMS_RE.match(token)
            pieces.append(_rebuild_dms(dms_match, rng, body) if dms_match else token)
        else:
            decimal_match = _DECIMAL_RE.match(token)
            pieces.append(_rebuild_decimal(decimal_match, rng) if decimal_match else token)
        position = match.end()
    pieces.append(body[position:])
    return label + "".join(pieces)


def _fake_sheet(value: str, rng: random.Random, attempt: int = 0) -> str:
    """图幅号：保留字母-数字-字母-数字的结构，内容整体重排。"""
    label, body = _split_label(value)
    text = body.strip()
    if re.fullmatch(r"[A-Z]\d{2}[A-Z]\d{6}", text):
        letters = "ABCDEFGHJKLMNPQRSTUVWXYZ"
        return label + (rng.choice(letters) + _digits(rng, 2)
                        + rng.choice(letters) + _digits(rng, 6))
    return label + _fake_code(body, rng)


def _fake_crs(value: str, rng: random.Random, attempt: int = 0) -> str:
    """坐标系名：换成另一个真实存在的坐标系名（不另外编造）。"""
    label, body = _split_label(value)
    text = body.strip()
    options = [crs for crs in _CRS_POOL if crs.upper() != text.upper()]
    return label + rng.choice(options or list(_CRS_POOL))


def _fake_record_no(value: str, rng: random.Random, attempt: int = 0) -> str:
    """病历号/客户号/流水号：保留标签，号码按原字符类别重排。"""
    label, body = _split_label(value)
    return label + _fake_code(body, rng)


def _fake_iban(value: str, rng: random.Random, attempt: int = 0) -> str:
    """IBAN：保留国家代码，重算 mod 97 校验位，保证格式合法。"""
    label, body = _split_label(value)
    compact = re.sub(r"\s", "", body).upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", compact):
        return label + _fake_code(body, rng)
    country = compact[:2]
    bban = "".join(rng.choice(_ALNUM) for _ in range(len(compact) - 4))
    # ISO 13616：把 BBAAN + 国家代码 + "00" 取 mod 97，校验位 = 98 - r
    check = 98 - _mod97(bban + country + "00")
    new = f"{country}{check:02d}{bban}"
    return label + (_remap(body, new) if " " in body else new)


def _fake_secret(value: str, rng: random.Random, attempt: int = 0) -> str:
    """密钥：同长度随机，保留 ``sk-`` 之类前缀。"""
    match = re.match(r"^([A-Za-z_\-]{2,10}[-_])", value)
    prefix = match.group(1) if match else ""
    rest = value[len(prefix):]
    return prefix + "".join(rng.choice(_ALNUM + "abcdefghijklmnopqrstuvwxyz")
                            for _ in range(len(rest)))


def _fake_dept(value: str, rng: random.Random, attempt: int = 0) -> str:
    """科室：保留"科/室/中心"后缀，换一个不指向具体诊断的科室名。"""
    for suffix in ("门诊", "病房", "中心", "科室", "科", "室"):
        if value.endswith(suffix) and len(value) > len(suffix):
            return "综合" + suffix
    return _fake_generic(value, rng)


def _fake_diagnosis(value: str, rng: random.Random, attempt: int = 0) -> str:
    """诊断：换成"待查"这类不泄露病种的占位。"""
    return "待查"


_FACTORIES = {
    "PERSON": _fake_person,
    "ORG": _fake_org,
    "ADDRESS": _fake_address,
    "PHONE": _fake_phone,
    "ID_CARD": _fake_id,
    "BANK": _fake_bank,
    "ACCOUNT": _fake_account,
    "AMOUNT": _fake_amount,
    "CASE_NO": _fake_case_no,
    "LICENSE": _fake_license,
    "USCC": _fake_uscc,
    "EMAIL": _fake_email,
    "PLATE": _fake_plate,
    "GEO_COORD": _fake_coord,
    "SHEET_NO": _fake_sheet,
    "CRS": _fake_crs,
    "CONTROL_POINT": _fake_record_no,
    "RECORD_NO": _fake_record_no,
    "IBAN": _fake_iban,
    "SECRET": _fake_secret,
    "DEPT": _fake_dept,
    "DIAGNOSIS": _fake_diagnosis,
}


def _canonical(etype: str) -> str:
    """实体类型归一化（PARTY/PATIENT 等别名统一进 PERSON 的会话命名空间）。"""
    key = (etype or "OTHER").strip().upper().replace("-", "_").replace(" ", "_")
    return _CANONICAL.get(key, key)


def _generate(ctx: ReplaceContext, etype: str, value: str,
              factory) -> str:
    """取会话内已有假值，没有就确定性生成并登记（含查重）。"""
    existing = _lookup(ctx, etype, value)
    if existing and existing != value:
        return existing
    rng = _rng_for(ctx, etype, value)
    needle = (value or "").strip()
    for _attempt in range(256):
        candidate = factory(value, rng, _attempt)
        if not candidate or candidate == value:
            continue
        # 隐私不变量：假值绝不能完整包含原文值（如地址只剩省市区层级时
        # 若原样保留前缀，会构成真实泄漏）。命中则换一个候选。
        if len(needle) >= 2 and needle in candidate:
            continue
        if _is_used(ctx, etype, candidate):
            continue
        _remember(ctx, etype, value, candidate)
        _mark_used(ctx, etype, candidate)
        return candidate
    fallback = _fake_generic(value, rng)
    if fallback == value and value:                    # 极端兜底：保证一定变
        last = value[-1]
        fallback = value[:-1] + ("某" if _is_chinese(last) else rng.choice("0123456789"))
    if len(needle) >= 2 and needle in fallback:         # 兜底同样不允许包含原文
        fallback = fallback[::-1]
    _remember(ctx, etype, value, fallback)
    return fallback


def build(rule: Rule, value: str, ctx: ReplaceContext) -> Decision:
    """生成保形假值，并保证同一会话里同一原值只映射一次。"""
    if value is None or value == "":
        return Decision(strategy="surrogate", replacement=value or "", black=False)
    raw_type = ctx.etype or (rule.entity if rule is not None else "") or "OTHER"
    etype = _canonical(raw_type)
    factory = _FACTORIES.get(etype)
    if factory is None:
        factory = lambda v, r, a: _fake_generic(v, r)   # noqa: E731
    replacement = _generate(ctx, etype, value, factory)
    return Decision(strategy="surrogate", replacement=replacement, black=False)


register_replacer("surrogate", build)

__all__ = ["build"]
