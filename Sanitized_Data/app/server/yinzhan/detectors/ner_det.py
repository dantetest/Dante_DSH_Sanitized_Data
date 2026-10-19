"""中文人名 / 地址 / 机构的启发式识别（不引入模型）。

为什么不用模型：产品要求"CPU 可跑、零外部依赖、不联网"，一个几 MB 的 NER
模型在 PDF 文本层质量参差时并不比规则稳。这里的取舍是**宁可少报**：
误报会让用户逐条拒绝，漏报用户可以框选补。

三类启发式：

- **人名**：常见百家姓 + 2–4 个汉字。前 6 字内出现"姓名/委托/当事/律师…"
  等提示词时置信度 0.85（accepted）；否则 0.55（pending，交给页面让用户确认）。
  另有两条高精度支路：``姓+先生/女士/律师`` 只把姓当敏感项（替换后仍是
  "陈先生"），``姓+某/某某`` 保留匿名指代结构。
- **地址**：行政区划层级 + 道路 + 门牌的组合，长度 ≥ 6，置信度 0.8。
- **机构**：以「公司/事务所/医院/银行/研究院/集团/中心/委员会/法院/检察院/
  支行/分行」结尾，长度 4–30，置信度 0.82。

否定过滤：只有姓没有名、``只/第/本/该`` 开头、纯数字/纯英文、以及落在
数字、英文、身份证、地址、机构等"已占用区间"里的人名一律不算。同一 value
在全文档多处命中会合并成一个 Entity（多个 spans）。

性能：结果按文档缓存（用文本指纹判断失效），同一文档被多条 ner 规则共用时
只算一遍。
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from typing import Iterable

from ..registry import Detector, iter_text_blocks, register_detector
from ..udm import DetectContext, Document, Entity, Rule, Span

# ────────────────────────────── 词典 ──────────────────────────────

# 常见单姓（百家姓主体）。用字符串是为了源码紧凑，set 后使用。
_SURNAME_CHARS = (
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
    "戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳酆鲍史唐"
    "费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟平黄"
    "和穆萧尹姚邵湛汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁"
    "杜阮蓝闵席季麻强贾路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍"
    "虞万支柯昝管卢莫经房裘缪干解应宗丁宣贲邓郁单杭洪包诸左石崔吉钮龚"
    "程嵇邢滑裴陆荣翁荀羊於惠甄麴家封芮羿储靳汲邴糜松井段富巫乌焦巴弓"
    "牧隗山谷车侯宓蓬全郗班仰秋仲伊宫宁仇栾暴甘钭厉戎祖武符刘景詹束龙"
    "叶幸司韶郜黎蓟薄印宿白怀蒲邰从鄂索咸籍赖卓蔺屠蒙池乔阴胥能苍双闻"
    "莘党翟谭贡劳逄姬申扶堵冉宰郦雍郤璩桑桂濮牛寿通边扈燕冀郏浦尚农温"
    "别庄晏柴瞿阎充慕连茹习宦艾鱼容向古易慎戈廖庾终暨居衡步都耿满弘匡"
    "国文寇广禄阙东欧殳沃利蔚越夔隆师巩厍聂晁勾敖融冷訾辛阚那简饶空曾"
    "毋沙乜养鞠须丰巢关蒯相查后荆红游竺权逯盖益桓公晋楚闫法汝鄢涂钦岳"
    "帅缑亢况郈有琴归海"
)
_SURNAMES: frozenset[str] = frozenset(_SURNAME_CHARS)

# 常见复姓（真实高频的；注意 东方/南宫 这类会撞普通词，只在 3 字以内使用）
_COMPOUND: frozenset[str] = frozenset({
    "欧阳", "司马", "上官", "诸葛", "东方", "皇甫", "尉迟", "公孙",
    "慕容", "长孙", "宇文", "司徒", "司空", "令狐", "夏侯", "独孤",
    "赫连", "澹台", "公冶", "申屠", "太史", "端木", "钟离", "闾丘",
    "鲜于", "拓跋", "呼延", "百里", "东郭", "万俟", "闻人", "轩辕", "西门",
})
# 只有这些复姓允许「复姓 + 双字名」= 4 字；其它情况人名最长 3 字，
# 否则「王建国房」这种会把后面的名词一起吃进来。
_COMPOUND_4: frozenset[str] = frozenset({
    "欧阳", "司马", "上官", "诸葛", "东方", "皇甫", "尉迟", "公孙",
    "慕容", "长孙", "宇文", "司徒", "令狐", "夏侯", "端木", "独孤",
    "南宫", "西门",
})

# 常见名字用字（人名候选的"名"必须全部落在这里；宁可少报，换低误报）
_COMMON_GIVEN: frozenset[str] = frozenset(
    "伟芳娜敏静丽强磊军洋勇艳杰娟涛明超秀霞平刚桂英华玉萍红娥玲芬燕"
    "彬宇浩凯鹏辉健俊帆帅婷雪琳颖慧佳思雨梦欣怡子文月乐天心志国建春"
    "东海波峰亮龙飞凤云成新生泽鑫悦瑶睿晨昊嘉宁远航然若诗语雅琪琦璇"
    "璐瑾瑜哲轩涵梓诺曦博学业舒雷梅兰菊香荣珍宝珠珍琳倩雯洁婷宏"
    "栋梁楠桐桦枫松柏森林忠孝仁义礼智信德善良诚朴素昌盛世泰安康"
    "福禄寿喜全美好妙巧灵捷聪光耀灿星辰山河川湖泉溪浪霜露虹彩青"
    "蓝紫绿双友群峰嵘岩磊森鑫淼焱垚"
    "淑晓蓉萍莉薇莎菲婷媛妍姝婉娴妤茜蕊蕾萌蓝彤丹瑜珊翠碧琼芝苓芸"
    "苗菁蔚菡蕙竹桃杏梨樱禾麦穗晴煦旭晖晶暖润溪泉河湖海洋涛波浪潮汐"
    "源潇淼树杨槐榆茂芊芃苑英蕴藤可言书章睿哲思学知才智慧悟理信义仁"
    "德善诚忠孝礼廉勇毅刚健安宁和平泰顺达通远航行舟帆鹏鸿雁鹤鹰龙凤"
    "麟虎豹骏"
)

# 高频"看着像人名其实是普通词"的否定表（按前缀匹配，宁可少报）
_BLACKLIST: frozenset[str] = frozenset("""
高兴 高级 高速 高额 高清 高压 高峰 高温 高院 高中 高考 高效 高估 高空
高架 高科 高端 高利 高价 高原 高强 高山 高标 高亮 高能
白色 白天 白云 白银 白金 白发 白菜 白酒 白衣 白纸 白班
黄金 黄牌 黄线 黄昏 黄河 黄山 黄浦 黄页 黄土
金额 金属 金融 金色 金牌 金价 金钱 金税 金卡 金库
银行 银牌 银河 银色 银保 银证
文书 文字 文件 文化 文明 文章 文本 文号 文体 文艺 文稿
方式 方法 方面 方向 方便 方案 方框 方块 方可 方才 方针
于是 由于 关于 对于 属于 位于 至于 等于 便于 善于 敢于 终于 过于
在于 源于 基于 限于 处于 用作 用于
任何 任务 任职 任意 任免 任期 任命 任选
何时 何处 何以 何况 何必 何人
林区 林业 林木 林子 林场 林带 林权
江面 江湖 江河 江山 江岸 江边 江西 江苏 江宁
张开 张望 张力 张罗 张狂 张贴 张挂 张榜 张弛
程序 程度 成员 成本 成功 成立 成套 成年 成品 成交
康复 康宁 康泰 康健
余地 余额 余下 余位 余数 余量 余款
宋体 宋词 宋代
简单 简洁 简称 简要 简明 简化
严重 严格 严肃 严厉 严密 严禁
尤其 范围 法院 法规 法律 法条 法官 法务 法定 法则 法人 法制
费用 费力 费时 费率 费款
谢谢 谢绝 谢意 谢礼
段落 段位 段长 断段
罗列 逻辑 罗盘 罗致
章节 章程 章法 章款
商品 商事 商量 商务 商行 商业 商定 商谈
客户 客服 客人 客观 客商 客栈
叶子 叶片 叶面 叶脉
龙头 龙湖 龙岩 龙山 龙岗 龙口 龙江 龙门
雷同 雷达 雷雨 雷暴 雷区
夏天 夏季 夏日 夏粮
田野 田间 田头 田埂 田亩
胡同 胡子 胡乱 胡话
凌乱 凌晨 凌空 凌汛
万一 万元 万能 万分 万全 万般
支持 支付 支票 支出 收支 支教 支行 支队 支配 支取
莫名 莫大 莫不
房屋 房间 房东 房产 房款 房贷 房型
解放 解决 解除 解密 解释 解答 解约
应有 应该 应急 应对 应用 应聘 应届
宣布 宣传 宣告 宣判 宣读 宣誓
郁闷 郁结
单位 单价 单据 单独 单向 单身 单方
洪水 洪涝 洪灾 洪峰
包括 包扎 包装 包干 包含 包庇 包销
左右 左派 左翼
石头 石油 石墨 石膏 石料 石化
吉日 吉祥 吉林 吉兆
陆续 陆地 陆军 陆路 陆运
荣誉 荣华 荣膺
羊肉 羊皮 羊绒
惠及 惠民 惠顾 优惠
家庭 家具 家属 家电 家人 家伙 家事
封闭 封存 封面 封口 封条 封顶
储备 储蓄 储存 储户 储能
松树 松动 松口 松散 松软
井盖 井口 井然 井区
富有 富裕 富力 富余 富足
焦点 焦急 焦炭 焦化
巴西 巴黎 巴望
牧业 牧场 牧民
山谷 山水 山地 山东 山西 山区 山脉 山坡
车辆 车站 车费 车号 车行 车流 车祸
全权 全部 全面 全额 全称 全国 全场 全程 全年
班长 班级 班次 班车
仰望 仰赖
秋收 秋季 秋风 秋色
伊始 伊人
宫廷 宫殿 宫室
宁静 宁夏 宁波 宁缺
仇人 仇恨 仇视
暴露 暴力 暴涨 暴利 暴雪
甘肃 甘愿 甘于
厉害 厉行
祖国 祖先 祖父 祖母
武断 武器 武术 武汉 武警 武力
符合 符号 符咒
景点 景色 景区 景观
束缚 束线
胜利 盛大 盛开 圣地 圣火
黎明 黎民
薄弱 薄利 薄片 薄膜
印刷 印章 印度 印鉴 印记
宿舍 住宿 宿营
怀化 怀念 怀疑 怀旧
从事 从来 从头 从严
索取 索引 索要 索赔
咸味 咸阳 咸菜
籍贯 籍此
依赖 赖账 赖皮
卓越 卓有 卓识
屠杀 屠夫
蒙古 蒙受 蒙面
池塘 池子 池水
乔木 乔迁 乔装
阴谋 阴影 阴天 阴雨
能源 能否 能手
苍白 苍茫 苍天
双方 双手 双重 双赢 双向
闻名 闻讯 新闻 闻见
党委 党员 党内 党纪
贡献 贡品
劳动 劳务 劳资 劳作
申报 申请 申明 申诉 申领
扶助 扶贫 扶持 扶手
堵塞 堵车 堵截
宰相 宰割
雍容
桑树 桑拿 桑蚕
桂林 桂花 桂圆
牛市 牛奶 牛肉
寿命 寿险 寿宴
通信 通知 通过 通行 通用 通航 通商 通宝
边疆 边际 边缘 边远
燕京 燕窝 燕山
浦发 浦东 浦口 浦江
尚未 尚可
农业 农村 农民 农产品
温度 温和 温暖 温泉
别处 别的 别人 别称 别墅
庄重 庄园 庄家 庄稼
柴油
充分 充足 充满 充实
羡慕 慕名 慕尼黑
连忙 连续 连接 连带 连绵
习惯 习俗 习作
艾滋 艾草
鱼类 鱼塘 鱼丸
容忍 容易 容积 容量
古代 古典 古董 古人
易货 易主 易燃 易碎
慎重
戈壁
终点 终端 终结
居民 居中 居住
衡量 衡阳 衡山 衡水
步行 步兵 步伐
都市 都好 都会
耿直 耿耿
满意 满足 满怀 满月
弘扬 弘法
匡扶
国际 国家 国民 国内 国外 国防 国企 国土
寇仇
广泛 广告 广场 广西 广州 广东 广博
东方 东北 东南 东京 东莞 东风 东城 东西
欧洲 欧式
沃土
利害 利润 利益 利率 利息 利用
蔚蓝 蔚然
越权 越南 越好 越级
隆冬 隆重
师傅 师生 师父 师长
巩固
勾结 勾当
融合 融资 融通
冷库 冷淡 冷静 冷藏
辛苦 辛勤 辛亏
那个 那些 那么 那样
饶恕 饶平
空白 空调 空间 空气 空前
曾经 曾用 曾任
沙盘 沙漠 沙滩 沙土
养老 养殖 养育
须知 须要
丰富 丰收 丰台 丰产
关闭 关系 关注 关键 关于 关联
相信 相同 相对 相关 相反 相等
查找 查询 查明 查验
后果 后续 后年 后台 后来
红色 红利 红旗 红头 红线
游戏 游览 游人
权利 权益 权威 权限
盖章 盖房 盖子
益阳 益智 益于
公共 公开 公正 公平 公益
晋升 晋级 晋中
楚国
法官 法律
涂料 涂抹
钦定 钦州
岳飞 岳阳
情况 状况 况且
有限公司 有限 有效 有关
海关 海洋 海口 海军 海里
""".split()
)
# 否定表按首字分桶：无上下文人名检查是热点，避免每次遍历整张表
_BLACKLIST_BY_FIRST: dict[str, tuple[str, ...]] = {}
for _word in _BLACKLIST:
    _BLACKLIST_BY_FIRST.setdefault(_word[0], ())       # type: ignore[arg-type]
_BLACKLIST_BY_FIRST = {
    first: tuple(w for w in _BLACKLIST if w[0] == first)
    for first in _BLACKLIST_BY_FIRST
}
# 人名前的上下文提示词（命中则置信度 0.85）
_HINTS: tuple[str, ...] = (
    "姓名", "名字", "委托", "当事", "法定代表", "代表", "代理", "联系", "经办",
    "律师", "法官", "医生", "医师", "患者", "病人", "甲方", "乙方", "丙方",
    "签字", "签名", "签收", "申请人", "被申请人", "被告人", "被告", "原告",
    "第三人", "证人", "报案人", "受害人", "被害人", "嫌疑人", "犯罪嫌疑",
    "家属", "联系人", "负责人", "董事", "监事", "经理", "主任", "护士",
    "教授", "老师", "先生", "女士", "同志", "案外人", "上诉人", "被上诉人",
    "申诉人", "举报人", "控告人", "承租人", "出租人", "买受人", "出卖人",
    "借款人", "出借人", "担保人", "投保人", "被保险人", "受益人", "收货人",
    "发货人", "收件人", "寄件人", "经办人", "审批人", "复核人", "制表人",
    "填表人", "作者", "编辑", "翻译", "口述", "陈述", "证言", "询问",
)
_HINT_WINDOW = 6

# 称谓（用于"姓 + 先生/女士"只取姓的场景）
_HONORIFICS: tuple[str, ...] = (
    "先生", "女士", "小姐", "同志", "律师", "法官", "医生", "医师", "护士",
    "主任", "经理", "教授", "老师", "师傅", "总监", "总经理", "董事长",
    "部长", "处长", "科长", "所长", "院长", "校长", "厂长", "行长", "总",
)

# 职务后缀（"李静怡合伙人律师"）：名字 + 职务 = 强证据，不需要前面的提示词
_ROLE_SUFFIXES: tuple[str, ...] = tuple(sorted({
    "合伙人律师", "执业律师", "主办律师", "副主任医师", "主任医师",
    "主治医师", "副主任", "总经理", "副总经理", "合伙人", "董事长",
    "工程师", "会计师", "设计师", "律师", "医师", "医生", "护士长",
    "护士", "主任", "经理", "总监", "教授", "副教授", "老师", "法官",
    "检察官", "书记员", "负责人", "经办人", "联系人",
}, key=len, reverse=True))

# 表格表头：xlsx/docx 单元格常常一行一个 block，靠表头给单元格里的名字提供上下文
_TABLE_HEADER_HINTS: tuple[str, ...] = (
    "客户名称", "客户姓名", "客户", "姓名", "名称", "户名", "法定代表人",
    "法人代表", "联系人", "负责人", "当事人", "患者姓名", "病人姓名",
    "代理人", "经办人", "担保人", "承租人", "买受人", "借款人", "投保人",
    "被保险人", "收款人", "付款人", "开票人", "制表人",
)
_HEADER_LOOKBACK = 12

# 姓名末字不能是这些虚词（否则"张伟和"之类的切分被接受）
_FUNCTION_TAIL: frozenset[str] = frozenset(
    "的了是在有和与及或称诉向为系由对从被将把等或签应可须需按经就都也"
    "还又而并但如因所其此该本我你他她它们吗呢吧啊呀哦嗯着过来去到说"
)

# 无上下文时，名字后面出现这些字才允许（强边界）。
# 由 Lead 联调确认：只允许标点/空白/数字/换行 + 常见虚词、动词；
# 特别注意不能加「房」——「王建国房屋买卖合同」会因此多吃一个字。
_FOLLOW_OK: frozenset[str] = frozenset(
    "，。、；：？！ \t\n\r（）()《》【】“”\"'的了是在有和与及或称诉向为"
    "系由对从被将把等或签应可须需按经就都也还又而并但如因所其此该本我"
    "你他她它们吗呢吧啊呀哦嗯着过来去到说之子女父母妻夫家属"
    "一先同已未不应予给让向至由当则若如即便虽"
)

# 无上下文时，名字前面必须是边界或这些虚词（如"公司的张伟""与张伟"）
_PRECEDE_OK: frozenset[str] = frozenset(
    "：:，,。；;、 \t\n\r（）()《》【】“”\"'的在为系是等和与及或称诉向对"
    "从被将把由让给同跟于如据按经就都也还又而并但"
)


# ────────────────────────────── 正则 ──────────────────────────────

# 硬性占用区：数字、英文、日期、金额、百分比。人名不能落在这些区间里。
_HARD_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"[0-9A-Za-z]+"),
    re.compile(r"\d[\d ,.\-]{3,}\d"),
    re.compile(r"(?:19|20)\d{2}\s*[-/年.]\s*\d{1,2}\s*[-/月.]\s*\d{1,2}\s*日?"),
    re.compile(r"[¥￥]\s*\d[\d,]*(?:\.\d+)?"),
    re.compile(r"\d+(?:\.\d+)?\s*[%％]"),
)

# 地址：行政区划链 + 道路 + 门牌
_ADMIN_UNIT = r"[\u4e00-\u9fa5]{1,8}?(?:自治区|特别行政区|省|市|地区|盟|区|县|旗|镇|乡|街道)"
_ADDR_RE = re.compile(
    r"(?:^|(?<=[：:，,。；;、\s（(【\[「“\"'《<>）)]))"
    + r"(?:" + _ADMIN_UNIT + r"){1,4}"
    + r"(?:[\u4e00-\u9fa5]{1,10}?(?:大道|胡同|路|街|巷|弄|里|段|村|小区|开发区|社区|园区))?"
    + r"(?:\d{1,5}(?:号|弄|栋|座|楼|单元|层|室)?)?"
    + r"(?:[\u4e00-\u9fa5\d]{1,8}?(?:号|弄|室|楼|栋|座|单元|层)){0,2}"
)
_ADMIN_TOKEN_RE = re.compile(
    r"[\u4e00-\u9fa5]{1,8}?(?:自治区|特别行政区|省|市|地区|盟|区|县|旗|镇|乡|街道)"
)
_ROAD_START_RE = re.compile(
    r"[\u4e00-\u9fa5]{1,10}?(?:大道|胡同|路|街|巷|弄|里|段|村|小区|开发区|社区|园区)"
)
# 地址后面紧跟这些词时说明是机构名的一部分，不算独立地址
_ORG_CONTINUATION: tuple[str, ...] = (
    "人民法院", "人民检察院", "医院", "卫生院", "妇幼保健院", "疾控中心",
    "公司", "事务所", "银行", "大学", "学院", "学校", "中学", "小学",
    "研究院", "研究所", "集团", "中心", "委员会", "法院", "检察院",
    "支行", "分行", "酒店", "宾馆", "商场", "超市", "工厂", "政府",
    "办事处", "管理局", "办公室",
)
# 地址开头的干扰词（"现住上海市…"里的"现住"）
_ADDR_LEAD_NOISE: tuple[str, ...] = (
    "送达地址", "联系地址", "办公地址", "注册地址", "户籍地址", "现住址",
    "家庭住址", "工作单位", "单位地址", "住所地", "户籍地", "居住地",
    "现居住", "现住", "现居", "家住", "住在", "居住于", "居于", "位于",
    "地处", "地址", "住址", "户籍", "籍贯", "所在", "系", "为", "在",
    "于", "住", "的", "是", "：", ":", "（", "(", "，", ",",
)

# 机构：后缀 + 向左回溯取字号
_ORG_SUFFIXES: tuple[str, ...] = tuple(sorted({
    "股份有限公司", "有限责任公司", "有限公司", "分公司", "子公司",
    "律师事务所", "会计师事务所", "税务师事务所", "专利事务所", "事务所",
    "人民检察院", "人民法院", "检察院", "法院",
    "研究院", "研究所", "设计院", "研究院", "科学院",
    "银行", "支行", "分行", "信用社",
    "大学", "学院", "学校", "中学", "小学", "幼儿园",
    "医院", "卫生院", "妇幼保健院", "疾控中心", "诊所",
    "集团", "控股", "公司", "委员会", "管理中心", "中心", "基金会", "协会",
    "商会", "联合会", "工作室", "办事处", "管理局", "监督局", "税务局",
}, key=len, reverse=True))
_ORG_SUFFIX_RE = re.compile("|".join(re.escape(s) for s in _ORG_SUFFIXES))
_ORG_RUN_RE = re.compile(r"[\u4e00-\u9fa5A-Za-z0-9（）()·&]")
_ORG_LEAD_NOISE: tuple[str, ...] = (
    "被告人", "原告人", "申请人", "被申请人", "委托人", "当事人", "第三人",
    "代理人", "被告", "原告", "申诉人", "上诉人", "被上诉人", "犯罪嫌疑人",
    "受害人", "被害人", "证人", "单位名称", "所在单位", "公司名称",
    "企业名称", "名称", "出租方", "承租方", "发包方", "承包方", "用人单位",
    "甲方", "乙方", "丙方", "买方", "卖方", "供方", "需方",
    "以下简称", "简称", "名称为", "名为", "称作", "即", "患者", "病人",
    "入住", "就诊于", "就诊", "复诊", "初诊", "前往", "送往", "送至",
    "转入", "转至", "来自", "工作于", "就职于", "隶属于", "服务于",
    "位于", "地处", "家住", "住在", "居住在", "现住", "注册", "登记",
    "办公", "经营", "承接", "委托", "代理", "聘请", "雇佣",
    "系", "为", "是", "由", "向", "在", "于", "该", "本", "其", "现", "的",
)
_ORG_BREAK_CHARS: frozenset[str] = frozenset(
    "与和及或称诉向为系由对从被将把等或的再又但而"
)

# 机构的「字号」必须有点实质内容，否则会把「有限公司」「建设有限公司」
# 这种碎片（甚至「国工商行政管理局」这种公共机构残片）当成敏感实体。
_ORG_GENERIC_TOKENS: tuple[str, ...] = tuple(sorted({
    "建设", "工程", "技术", "咨询", "服务", "发展", "实业", "商贸", "科技",
    "文化", "投资", "管理", "有限", "股份", "责任", "集团", "公司", "中心",
    "研究", "设计", "勘察", "监理", "施工", "劳务", "派遣", "物资", "贸易",
    "电子", "信息", "网络", "环保", "能源", "交通", "行政", "工商", "税务",
    "监督", "人民", "国家", "中华", "中国",
}, key=len, reverse=True))


def _org_plausible(value: str) -> bool:
    """判断一个候选机构名是不是「有具体字号」的真名字。"""
    name = value
    for suf in _ORG_SUFFIXES:
        if value.endswith(suf):
            name = value[: -len(suf)]
            break
    name = name.strip("（）()·& 　")
    if len(name) < 2:
        return False
    rest = name
    for tok in _ORG_GENERIC_TOKENS:
        rest = rest.replace(tok, "")
    # 去掉行业通用词之后还得剩至少两个字，才算有字号
    return len(rest.strip("（）()·& 　")) >= 2


# ────────────────────────────── 缓存结构 ──────────────────────────────

@dataclass
class _Candidate:
    """一种启发式命中的原始结果（跨 block 已按 value 合并）。"""

    kind: str                              # person | address | org
    value: str
    spans: list[Span] = field(default_factory=list)
    confidence: float = 0.8
    status: str = "accepted"
    hints: frozenset[str] = frozenset()


_CACHE: dict[tuple[str, tuple], dict[str, list[_Candidate]]] = {}
_CACHE_ORDER: list[tuple[str, tuple]] = []
_CACHE_MAX = 8
_CACHE_LOCK = threading.Lock()


def _is_chinese(ch: str) -> bool:
    return "\u4e00" <= ch <= "\u9fff"


def _match_any(text: str, pos: int, words: Iterable[str]) -> str | None:
    for word in words:
        if text.startswith(word, pos):
            return word
    return None


def _overlap_any(spans: Iterable[tuple[int, int]], start: int, end: int) -> bool:
    """区间是否与任一段重叠。"""
    for span_start, span_end in spans:
        if start < span_end and span_start < end:
            return True
    return False


def _in_blacklist(candidate: str) -> bool:
    """否定表：等值、或以否定词为前缀、或本身是否定词的前缀。

    按首字分桶查，避免每个候选都遍历整张表（性能热点）。
    """
    if candidate in _BLACKLIST:
        return True
    for word in _BLACKLIST_BY_FIRST.get(candidate[0], ()):
        if candidate.startswith(word) or word.startswith(candidate):
            return True
    return False


class _BlockScanner:
    """单个 block 内的人名/地址/机构候选提取。"""

    def __init__(self, text: str) -> None:
        self.text = text
        self.len = len(text)
        self.hard: list[tuple[int, int]] = []
        self.addresses: list[tuple[int, int]] = []
        self.orgs: list[tuple[int, int]] = []
        self.occupied: list[tuple[int, int]] = []

    # ── 工具 ──
    def _overlaps(self, spans: list[tuple[int, int]], start: int, end: int) -> bool:
        for s, e in spans:
            if start < e and s < end:
                return True
        return False

    def _is_boundary_before(self, pos: int) -> bool:
        if pos <= 0:
            return True
        ch = self.text[pos - 1]
        if not _is_chinese(ch):
            return not ch.isalnum()
        return ch in _PRECEDE_OK

    def _is_boundary_after(self, pos: int) -> bool:
        """名字右侧必须是"词尾"：标点/空白/数字/换行，或常见虚词动词。

        PDF 抽取经常在字之间插空格，所以这里对非汉字只排斥英文字母。
        """
        if pos >= self.len:
            return True
        ch = self.text[pos]
        if not _is_chinese(ch):
            return not ch.isalpha()
        return ch in _FOLLOW_OK

    def _hints_before(self, pos: int) -> frozenset[str]:
        """前 6 个字符内的上下文提示词。

        PDF 文本层会把"承办 法官"抽成"承 办 法 官"，所以先去掉窗口里的空白
        再匹配，否则"法官/律师"这类提示词永远命不中。
        """
        window = self.text[max(0, pos - _HINT_WINDOW):pos]
        squashed = re.sub(r"\s+", "", window)
        found = {hint for hint in _HINTS if hint in squashed}
        return frozenset(found)

    # ── 主入口 ──
    def scan(self) -> None:
        for regex in _HARD_RES:
            self.hard.extend((m.start(), m.end()) for m in regex.finditer(self.text))
        self.addresses = self._find_addresses()
        self.orgs = self._find_orgs()
        self.occupied = self.hard + self.addresses + self.orgs

    # ── 地址 ──
    def _find_addresses(self) -> list[tuple[int, int]]:
        out: list[tuple[int, int]] = []
        for match in _ADDR_RE.finditer(self.text):
            start, end = match.start(), match.end()
            # 去掉"现住/地址："等前导干扰词
            changed = True
            while changed:
                changed = False
                for noise in _ADDR_LEAD_NOISE:
                    if self.text.startswith(noise, start):
                        start += len(noise)
                        changed = True
                        break
            value = self.text[start:end].strip()
            if not value:
                continue
            end = start + len(value)
            if end - start < 6:
                continue
            # 行政区划必须成链（>=2 级），否则要有道路 + 门牌结构
            units = 0
            pos = 0
            while pos < len(value):
                unit = _ADMIN_TOKEN_RE.match(value, pos)
                if not unit:
                    break
                pos = unit.end()
                units += 1
            road = _ROAD_START_RE.match(value)
            if units < 2 and not (road and re.search(r"\d", value)):
                continue
            # 机构名的一部分不算独立地址
            if _match_any(self.text, end, _ORG_CONTINUATION):
                continue
            if self.text[end:end + 1] == "路" and units < 2:
                continue
            out.append((start, end))
        return self._dedupe_longest(out)

    # ── 机构 ──
    def _find_orgs(self) -> list[tuple[int, int]]:
        raw: list[tuple[int, int]] = []
        for match in _ORG_SUFFIX_RE.finditer(self.text):
            suffix_start, suffix_end = match.start(), match.end()
            # 向左吃字号
            left = suffix_start
            while (left > 0 and suffix_start - left < 26
                   and _ORG_RUN_RE.match(self.text[left - 1])):
                ch = self.text[left - 1]
                if ch in _ORG_BREAK_CHARS:
                    break
                left -= 1
            # 向右吃括号后缀（如"（普通合伙）"）
            right = suffix_end
            bracket = re.match(r"[（(][^（）()]{1,12}[）)]", self.text[right:])
            if bracket:
                right += bracket.end()
            value = self.text[left:right]
            # 去掉前导干扰词
            changed = True
            while changed:
                changed = False
                for noise in _ORG_LEAD_NOISE:
                    if value.startswith(noise) and len(value) - len(noise) >= 4:
                        value = value[len(noise):]
                        left += len(noise)
                        changed = True
                        break
            # 中间断词符：取最后一个断词符之后的部分（如"张三与甲公司"）
            for idx, ch in enumerate(value):
                if ch in _ORG_BREAK_CHARS and 0 < idx:
                    rest = value[idx + 1:]
                    if len(rest) >= 4:
                        value = rest
                        left = left + idx + 1
                    break
            if 4 <= len(value) <= 30 and _org_plausible(value):
                raw.append((left, left + len(value)))
        return self._dedupe_longest(raw)

    @staticmethod
    def _dedupe_longest(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
        """重叠区间只保留更长的那个。"""
        kept: list[tuple[int, int]] = []
        for start, end in sorted(spans, key=lambda s: (-(s[1] - s[0]), s[0])):
            if any(start < ke and ks < end for ks, ke in kept):
                continue
            kept.append((start, end))
        return sorted(kept)

    # ── 人名 ──
    def _find_persons(self) -> list[tuple[int, int, float, bool, frozenset[str]]]:
        text = self.text
        hits: list[tuple[int, int, float, bool, frozenset[str]]] = []
        i = 0
        while i < self.len:
            sl = 0
            if text[i:i + 2] in _COMPOUND:
                sl = 2
            elif text[i] in _SURNAMES:
                sl = 1
            if not sl:
                i += 1
                continue
            if self._overlaps(self.occupied, i, i + sl):
                i += sl
                continue

            hints = self._hints_before(i)
            strong = bool(hints)
            surname_end = i + sl

            # 支路 1：姓 + 称谓（张先生 / 王律师）——只把姓当敏感项
            honorific = _match_any(text, surname_end, _HONORIFICS) if sl == 1 else None
            if honorific and (strong or self._is_boundary_before(i)):
                if not self._overlaps(self.occupied, i, surname_end):
                    hits.append((i, surname_end, 0.82, True, hints or frozenset({"称谓"})))
                    i = surname_end
                    continue

            # 支路 2：姓 + 某（张某 / 欧阳某某）
            if surname_end < self.len and text[surname_end] == "某":
                end = surname_end + 1
                while end < self.len and text[end] == "某" and end - i < 4:
                    end += 1
                if not self._overlaps(self.occupied, i, end):
                    hits.append((i, end, 0.80, True, hints or frozenset({"匿名指代"})))
                    i = end
                    continue

            # 支路 3：常规姓名，长优先。
            # 长度上限 3；只有 _COMPOUND_4 里的复姓才允许"复姓+双字名"= 4 字。
            run_end = surname_end
            while run_end < self.len and _is_chinese(text[run_end]) and run_end - i < 5:
                run_end += 1
            compound_surname = text[i:i + 2]
            max_total = 4 if (sl == 2 and compound_surname in _COMPOUND_4) else 3
            chosen: tuple[int, int, float, bool, frozenset[str]] | None = None
            for total in range(min(max_total, run_end - i), sl, -1):
                cand = text[i:i + total]
                if not cand or cand[-1] in _FUNCTION_TAIL:
                    continue
                # 3 字候选的第 2、3 字（复姓为第 3、4 字）必须在常见名字用字里
                if any(ch not in _COMMON_GIVEN for ch in cand[sl:]):
                    continue
                if self._overlaps(self.occupied, i, i + total):
                    continue
                # 名字后面紧跟职务（"李静怡合伙人律师"）也是强证据
                role = _match_any(text, i + total, _ROLE_SUFFIXES) if total >= 2 else None
                # 右侧必须是词尾：没有候选满足就整条丢弃（宁可不报）
                if not self._is_boundary_after(i + total) and not role:
                    continue
                if not strong and not role and not self._is_boundary_before(i):
                    continue
                if _in_blacklist(cand):
                    continue
                if strong or role:
                    chosen = (i, i + total, 0.85, True,
                              hints or frozenset({"职务"} if role else ()))
                    break
                chosen = (i, i + total, 0.55, False, hints)
                break
            if chosen:
                hits.append(chosen)
                i = chosen[1]
            else:
                i += 1
        return hits


class HeuristicNerDetector(Detector):
    """启发式中文人名 / 地址 / 机构检测器。"""

    id = "ner"

    KIND_TO_ENTITY = {"person": "PERSON", "address": "ADDRESS", "org": "ORG"}

    def __init__(self, types: Iterable[str] | None = None,
                 required_hints: Iterable[str] | None = None) -> None:
        wanted: list[str] = []
        for raw in (types or ("person", "address", "org")):
            key = str(raw).strip().lower()
            if key in self.KIND_TO_ENTITY and key not in wanted:
                wanted.append(key)
        if not wanted:
            wanted = ["person", "address", "org"]
        self.types: tuple[str, ...] = tuple(wanted)
        self.required_hints: tuple[str, ...] = tuple(
            str(h) for h in (required_hints or ()) if str(h).strip()
        )

    @classmethod
    def from_spec(cls, spec: dict) -> "HeuristicNerDetector":
        """从 yaml 的 detect 项创建检测器。

        支持：``types`` / ``type`` 冲突时以 ``types`` 为准；
        ``required_hints`` 让"当事人"这类规则只命中带指定上下文的候选。
        """
        if not isinstance(spec, dict):
            raise ValueError(f"HeuristicNerDetector.from_spec 需要 dict，得到 {type(spec).__name__}")
        types = spec.get("types") or spec.get("entities") or spec.get("kinds")
        if isinstance(types, str):
            types = [types]
        required = spec.get("required_hints") or spec.get("near")
        if isinstance(required, str):
            required = [required]
        return cls(types=types, required_hints=required)

    # ── 抽取 ──
    @staticmethod
    def _fingerprint(doc: Document) -> tuple:
        prints: list[tuple[str, int]] = []
        for _page, block in doc.all_blocks():
            if block.kind == "text" and block.text:
                prints.append((block.id, hash(block.text)))
        return tuple(prints)

    def _extract(self, doc: Document) -> dict[str, list[_Candidate]]:
        """抽取（带文档级缓存），返回 kind -> candidates。"""
        key = (getattr(doc, "doc_id", "") or "", self._fingerprint(doc))
        with _CACHE_LOCK:
            cached = _CACHE.get(key)
            if cached is not None:
                return cached

        merged: dict[tuple[str, str], _Candidate] = {}
        block_texts: list[tuple[str, str]] = []
        protected: dict[str, list[tuple[int, int]]] = {}
        for page in doc.pages:
            page_blocks = [b for b in page.blocks if b.kind == "text" and b.text]
            # 表头 block 的位置：xlsx/docx 表格一行一个 block，单元格里的名字
            # 可以借附近表头（客户名称/姓名/法定代表人…）获得上下文。
            header_positions = [
                index for index, block in enumerate(page_blocks)
                if any(hint in re.sub(r"\s+", "", block.text) for hint in _TABLE_HEADER_HINTS)
            ]
            for block_index, block in enumerate(page_blocks):
                text = block.text
                block_texts.append((block.id, text))
                scanner = _BlockScanner(text)
                scanner.scan()
                protected[block.id] = scanner.hard + scanner.addresses + scanner.orgs
                near_header = any(
                    0 <= block_index - position <= _HEADER_LOOKBACK
                    for position in header_positions
                )
                # 人名
                for start, end, conf, strong, hints in scanner._find_persons():
                    value = text[start:end]
                    # 表格单元格"整块就是一个姓名"且附近有表头 -> 视为有上下文
                    if not strong and near_header and text.strip() == value:
                        strong, conf = True, 0.85
                        hints = hints or frozenset({"表头"})
                    cand = merged.get(("person", value))
                    if cand is None:
                        cand = _Candidate(kind="person", value=value, confidence=0.0)
                        merged[("person", value)] = cand
                    cand.spans.append(Span(block_id=block.id, start=start, end=end))
                    cand.confidence = max(cand.confidence, conf)
                    cand.hints = cand.hints | hints
                    if strong:
                        cand.status = "accepted"
                # 地址 / 机构
                for kind, spans in (("address", scanner.addresses), ("org", scanner.orgs)):
                    for start, end in spans:
                        value = text[start:end]
                        cand = merged.get((kind, value))
                        if cand is None:
                            cand = _Candidate(kind=kind, value=value,
                                              confidence=0.80 if kind == "address" else 0.82)
                            merged[(kind, value)] = cand
                        cand.spans.append(Span(block_id=block.id, start=start, end=end))

        # 同值扩散：人名右侧边界检查会丢弃"王建国房屋"里的那一次命中，
        # 但同一个名字已在别处被接受时，全文同值出现都要补 span——
        # 否则导出成品里会残留原文，出口自检会拦下来。
        for cand in merged.values():
            if cand.kind != "person" or cand.status != "accepted" or len(cand.value) < 2:
                continue
            existing = {(s.block_id, s.start) for s in cand.spans}
            for block_id, text in block_texts:
                start = text.find(cand.value)
                while start >= 0:
                    end = start + len(cand.value)
                    if ((block_id, start) not in existing
                            and not _overlap_any(protected.get(block_id, ()), start, end)):
                        cand.spans.append(Span(block_id=block_id, start=start, end=end))
                    start = text.find(cand.value, end)

        result: dict[str, list[_Candidate]] = {kind: [] for kind in self.KIND_TO_ENTITY}
        for cand in merged.values():
            if cand.status == "pending" and cand.kind != "person":
                cand.status = "accepted"
            result.setdefault(cand.kind, []).append(cand)

        with _CACHE_LOCK:
            _CACHE[key] = result
            _CACHE_ORDER.append(key)
            while len(_CACHE_ORDER) > _CACHE_MAX:
                old = _CACHE_ORDER.pop(0)
                _CACHE.pop(old, None)
        return result

    # ── 检测 ──
    def detect(self, doc: Document, rule: Rule,
               ctx: DetectContext) -> list[Entity]:
        """按规则声明的类型输出实体。"""
        extracted = self._extract(doc)
        wanted_entity = {self.KIND_TO_ENTITY[k] for k in self.types}
        entities: list[Entity] = []
        for kind in self.types:
            for cand in extracted.get(kind, []):
                if self.required_hints and not (cand.hints & set(self.required_hints)):
                    continue
                etype = self.KIND_TO_ENTITY[kind]
                # 规则的 entity 与启发式类型一致时用规则写法（大小写/别名）
                if rule.entity and rule.entity.upper() in wanted_entity:
                    if rule.entity.upper() == etype:
                        etype = rule.entity.upper()
                entities.append(Entity(
                    id=Entity.new_id(),
                    etype=etype,
                    label=rule.label,
                    value=cand.value,
                    spans=[Span(block_id=s.block_id, start=s.start, end=s.end)
                           for s in cand.spans],
                    level=rule.level,
                    confidence=cand.confidence,
                    origin="model",
                    status=cand.status,
                ))
        return entities


register_detector(HeuristicNerDetector)

__all__ = ["HeuristicNerDetector"]
