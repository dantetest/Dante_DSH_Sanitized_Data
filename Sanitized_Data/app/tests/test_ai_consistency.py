# -*- coding: utf-8 -*-
"""Phase A2：跨字段/跨页近似原值的假值一致性（AI 换名版）自测。

背景
----
同一真实实体在 OCR/跨页场景会被识别成多个「近似变体」实体（公司名少一字、
中间夹空格、扫描误识等）。修复前每个变体各自换名，AI 读到的文档出现多个不同
假名，无法确认是同一个主体（doc05 甲方名、doc06/doc07 地址等）。

本测试锁定 `pipeline.consolidate_surrogates()` 的行为（全部虚构数据）：

1. 同类型近似原值（规范化相等 / 包含且长度比>=0.85 / difflib 相似度>=0.88 且
   长度差<=4）归为一簇，统一使用「最完整原值」的假值；并查集处理链式归并；
2. 阈值固化（0.85 / 0.88 / 长度差 4）；
3. canonical = 规范化后最长，并列取原值最长；canonical 的假值赋给簇内所有实体；
4. 不同实体 / 不同类型 / AMOUNT 不误并；black / keep / rejected / 非 surrogate
   的实体一律不动；
5. session.map 为每个变体写入「原值→统一假值」，可落 vault 供 rehydrate；
6. 函数幂等（重复调用不再改变任何决策/映射）。

Phase A2-Refine 追加（Lead 决策）：
7. ADDRESS 放宽判据（包含比>=0.70 / difflib>=0.85 且长度差<=5）+ 行政区前缀守卫
  （去掉「自治区/自治州/特别行政区」等后缀后前 4 字相同）；
8. ORG 简称别名（剥后缀得 core，短 core 是长 core 前缀且短 core>=2 字），
   误检词黑名单（按照/可以/函请/业主/通知/方有权/管理/公章/我公司/贵公司…）禁止并入。

运行（Windows 控制台请带 -X utf8）：

    python -X utf8 app/tests/test_ai_consistency.py

只用系统临时目录（YINZHAN_DATA_DIR），不写仓库 data/。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

# 必须先定数据目录，再 import yinzhan（config 在 import 时解析路径）。
_TMP = Path(tempfile.mkdtemp(prefix="yz_ai_consistency_"))
os.environ["YINZHAN_DATA_DIR"] = str(_TMP / "data")

REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / "app" / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

PASS_COUNT = 0
FAILS: list[tuple[str, str]] = []

#: 测试夹具用到的字符串（全部虚构；不取自真实测试集）
ORG_A = "四川华鑫建材有限公司"                  # 10 字
ORG_B = "四川华鑫建村有限公司"                  # 10 字，1 字 OCR 误识（材→村）
ORG_A_SPACED = "四川 华鑫 建材有限公司"         # 规范化后与 ORG_A 相同，原值更长
ORG_LONG = "云南远达物资集团有限公司"           # 12 字（另一簇）
ORG_TRUNC = "云南远达物资集团有限公"            # 11 字（ORG_LONG 少一字）
ORG_TAIL3 = "四川华鑫建材有限公司分公司"        # 13 字（包含 ORG_A，长度比 <0.85）
ADDR_A = "成都市武侯区人民南路四段一号"
ADDR_B = ADDR_A + "附楼"
ADDR_C = ADDR_B + "二单元三号楼"
ADDR_D = ADDR_A + "ABCDE"                       # 与 ADDR_A 长度差 5
ORG_OTHER = "北京远达贸易有限公司"

# ADDRESS 变体（Phase A2-Refine 目标场景；同构的公开地名串，不含个人信息）
ADDR_TIBET_1 = "西藏日喀则地区亚东县2017"
ADDR_TIBET_2 = "西藏自治区日喀则地区亚东县"
ADDR_TIBET_3 = "西藏日喀则地区亚东县"
ADDR_TIBET_OTHER = "云南省日喀则地区亚东县2017"   # 仅前 2 字不同：不得误并
ADDR_TIBET_NO_PROV = "日喀则地区亚东县2017"        # 缺省级前缀（doc07 同类变体）
# ORG 简称别名（Phase A2-Refine 目标场景）
ORG_ALIAS_LONG = "云基智慧工程股份有限公司"
ORG_ALIAS_SHORT = "云基公司"
ORG_ALIAS_OTHER = "远达贸易有限公司"


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS_COUNT
    if ok:
        PASS_COUNT += 1
        print(f"  [PASS] {name}" + (f"  {detail}" if detail else ""), flush=True)
    else:
        FAILS.append((name, detail))
        print(f"  [FAIL] {name}  {detail}", flush=True)


# ────────────────────────────── 夹具 ──────────────────────────────

def _ent(etype: str, value: str, replacement: str | None, *,
         status: str = "accepted", strategy: str = "surrogate",
         black: bool = False):
    from yinzhan.udm import Decision, Entity, Span

    ent = Entity(id=Entity.new_id(), etype=etype, label=etype, value=value,
                 spans=[Span(block_id="b0", start=0, end=len(value))])
    ent.status = status
    ent.decision = Decision(strategy=strategy, replacement=replacement, black=black)
    return ent


def _repls(entities) -> dict[str, str]:
    return {e.value: (e.decision.replacement if e.decision else None)
            for e in entities}


def _snapshot(entities, session) -> tuple[dict, dict]:
    decisions = {e.id: (e.decision.strategy, e.decision.replacement,
                        bool(e.decision.black))
                 for e in entities if e.decision}
    return decisions, dict(session.map)


# ────────────────────────────── 用例 ──────────────────────────────

def test_thresholds(pipeline) -> None:
    print("\n[1] 阈值固化 / 规范化", flush=True)
    check("包含判据长度比阈值 = 0.85",
          pipeline.CONSOLIDATE_CONTAIN_RATIO == 0.85,
          str(pipeline.CONSOLIDATE_CONTAIN_RATIO))
    check("difflib 相似度阈值 = 0.88",
          pipeline.CONSOLIDATE_SIM_THRESHOLD == 0.88,
          str(pipeline.CONSOLIDATE_SIM_THRESHOLD))
    check("相似度判据长度差上限 = 4",
          pipeline.CONSOLIDATE_MAX_LEN_DIFF == 4,
          str(pipeline.CONSOLIDATE_MAX_LEN_DIFF))
    check("AMOUNT 在排除类型里",
          "AMOUNT" in pipeline.CONSOLIDATE_EXCLUDED_TYPES)

    norm = pipeline._consolidate_norm
    check("规范化：NFKC 全角→半角",
          norm("ＡＢＣ１２３") == "ABC123", norm("ＡＢＣ１２３"))
    check("规范化：去空白/中英文标点",
          norm("四川，华鑫。建材（有限公司） A-B_C") == "四川华鑫建材有限公司ABC",
          norm("四川，华鑫。建材（有限公司） A-B_C"))

    similar = pipeline._consolidate_similar
    check("边界：1 字误识（sim=0.90）→ 近似",
          similar(norm(ORG_A), norm(ORG_B)))
    check("边界：包含且长度比 0.917 → 近似",
          similar(norm(ORG_LONG), norm(ORG_TRUNC)))
    check("边界：包含但长度比 0.769、sim=0.870 → 不近似",
          not similar(norm(ORG_A), norm(ORG_TAIL3)))
    check("边界：sim>=0.88 但长度差=5 → 不近似（长度差闸门）",
          not similar(norm(ADDR_C), norm(ADDR_C + "ABCDE")))
    check("边界：链式两端 A!~C（sim=0.800、长度比 0.667）→ 不直接近似",
          not similar(norm(ADDR_A), norm(ADDR_C)))

    # Phase A2-Refine 新增判据的阈值固化
    check("ADDRESS 包含长度比阈值 = 0.70",
          pipeline.ADDRESS_CONTAIN_RATIO == 0.70,
          str(pipeline.ADDRESS_CONTAIN_RATIO))
    check("ADDRESS difflib 阈值 = 0.85",
          pipeline.ADDRESS_SIM_THRESHOLD == 0.85,
          str(pipeline.ADDRESS_SIM_THRESHOLD))
    check("ADDRESS 长度差上限 = 5",
          pipeline.ADDRESS_MAX_LEN_DIFF == 5,
          str(pipeline.ADDRESS_MAX_LEN_DIFF))
    check("ADDRESS 行政区前缀最少公共字符 = 4",
          pipeline.ADDRESS_PREFIX_MIN == 4,
          str(pipeline.ADDRESS_PREFIX_MIN))
    check("ORG 简称最短 core 长度 = 2",
          pipeline.ORG_MIN_CORE_LEN == 2, str(pipeline.ORG_MIN_CORE_LEN))
    check("ORG 后缀表含公司/事务所/法院/医院/银行/支行/中心/集团",
          {"公司", "股份有限公司", "有限责任公司", "事务所", "法院",
           "检察院", "医院", "银行", "支行", "中心", "集团"}
          <= set(pipeline.ORG_SUFFIXES))
    check("ORG 误检词黑名单含 Lead 指定词",
          {"按照", "可以", "函请", "业主", "通知", "方有权", "管理",
           "公章", "我公司", "贵公司"} <= set(pipeline.ORG_ALIAS_STOPWORDS))


def test_org_merge(pipeline) -> None:
    print("\n[2] ORG 近似变体归并（含包含关系 / 相似度）", flush=True)
    ents = [
        _ent("ORG", ORG_A, "假公司甲"),
        _ent("ORG", ORG_B, "假公司乙"),          # 1 字误识，与 ORG_A 同簇
        _ent("ORG", ORG_LONG, "假公司丙"),
        _ent("ORG", ORG_TRUNC, "假公司丁"),      # 与 ORG_LONG 同簇
        _ent("ORG", ORG_OTHER, "假公司戊"),      # 无关实体，不应被并
    ]
    session = pipeline.Session()
    stats = pipeline.consolidate_surrogates(ents, {}, session)

    replacements = _repls(ents)
    check("ORG：统计 groups=2 / members=4",
          stats["groups"] == 2 and stats["members"] == 4, str(stats))
    check("ORG：误识变体统一为同一假值",
          replacements[ORG_A] == replacements[ORG_B] is not None,
          f"{replacements[ORG_A]} / {replacements[ORG_B]}")
    check("ORG：包含关系变体统一为同一假值",
          replacements[ORG_LONG] == replacements[ORG_TRUNC] is not None,
          f"{replacements[ORG_LONG]} / {replacements[ORG_TRUNC]}")
    check("ORG：无关公司不被误并",
          replacements[ORG_OTHER] == "假公司戊"
          and replacements[ORG_OTHER] not in (replacements[ORG_A], replacements[ORG_LONG]),
          str(replacements[ORG_OTHER]))
    check("ORG：每个变体都写入 session.map",
          all(session.map.get(("ORG", v)) == rep
              for v, rep in ((ORG_A, replacements[ORG_A]),
                             (ORG_B, replacements[ORG_B]),
                             (ORG_LONG, replacements[ORG_LONG]),
                             (ORG_TRUNC, replacements[ORG_TRUNC]))),
          str(session.to_dict().get("ORG", {}).keys()))
    check("ORG：无关实体不写入 map",
          ("ORG", ORG_OTHER) not in session.map)
    check("ORG：mark_used 记录统一假值",
          replacements[ORG_A] in session.used.get("ORG", set()))


def test_canonical(pipeline) -> None:
    print("\n[3] canonical 选择（最长/最完整）", flush=True)
    # 规范化后等长（10），原值更长（12，含空格）者优先
    ents = [_ent("ORG", ORG_A, "假值-短原值"),
            _ent("ORG", ORG_A_SPACED, "假值-长原值")]
    session = pipeline.Session()
    pipeline.consolidate_surrogates(ents, {}, session)
    check("canonical：规范化等长时取原值最长者的假值",
          ents[0].decision.replacement == ents[1].decision.replacement == "假值-长原值",
          f"{ents[0].decision.replacement} / {ents[1].decision.replacement}")
    check("canonical：短原值变体的 map 也指向 canonical 假值",
          session.map.get(("ORG", ORG_A)) == "假值-长原值",
          str(session.map.get(("ORG", ORG_A))))
    check("canonical：canonical 映射键排簇内最前（vault 还原最完整原值）",
          next(iter(session.map)) == ("ORG", ORG_A_SPACED),
          str(next(iter(session.map))))

    # 规范化长度不同：取规范化后最长者的假值
    ents2 = [_ent("ORG", ORG_TRUNC, "假值-截断"),
             _ent("ORG", ORG_LONG, "假值-完整")]
    pipeline.consolidate_surrogates(ents2, {}, pipeline.Session())
    check("canonical：取规范化后最长者的假值",
          ents2[0].decision.replacement == ents2[1].decision.replacement == "假值-完整",
          f"{ents2[0].decision.replacement} / {ents2[1].decision.replacement}")


def test_chain(pipeline) -> None:
    print("\n[4] 链式归并（A~B、B~C，A 与 C 不直接近似）", flush=True)
    pair = pipeline._consolidate_pair
    a, b, c = (pipeline._consolidate_norm(v) for v in (ADDR_A, ADDR_B, ADDR_C))
    check("链式：A~B 相邻近似", pair(a, b, "ADDRESS"))
    check("链式：B~C 相邻近似", pair(b, c, "ADDRESS"))
    check("链式：A!~C 不直接近似（只能靠并查集串成一簇）",
          not pair(a, c, "ADDRESS"))
    ents = [_ent("ADDRESS", ADDR_A, "假地址甲"),
            _ent("ADDRESS", ADDR_B, "假地址乙"),
            _ent("ADDRESS", ADDR_C, "假地址丙")]
    session = pipeline.Session()
    stats = pipeline.consolidate_surrogates(ents, {}, session)
    repls = {e.decision.replacement for e in ents}
    check("链式：3 个变体并入同一簇", stats["groups"] == 1
          and stats["members"] == 3, str(stats))
    check("链式：3 个变体统一为 canonical（最长原值）的假值",
          len(repls) == 1 and ents[2].decision.replacement in repls,
          str(repls))
    check("链式：三个变体都写入 session.map",
          all(("ADDRESS", v) in session.map for v in (ADDR_A, ADDR_B, ADDR_C)),
          str(list(session.map)))
    check("链式：canonical（最长地址）映射键排簇内最前",
          next(iter(session.map)) == ("ADDRESS", ADDR_C),
          str(next(iter(session.map))))


def test_address_variants(pipeline) -> None:
    print("\n[5] ADDRESS 变体（放宽阈值 + 行政区前缀，doc06 同类）", flush=True)
    norm = pipeline._consolidate_norm
    n1, n2, n3 = (norm(v) for v in (ADDR_TIBET_1, ADDR_TIBET_2, ADDR_TIBET_3))
    check("字面前缀只有 2 字（自治区 有/无 的差异）",
          pipeline._shared_prefix_len(n1, n2) == 2,
          str(pipeline._shared_prefix_len(n1, n2)))
    check("行政区后缀归一后视为同一行政区前缀",
          pipeline._address_prefix_ok(n1, n2)
          and pipeline._address_prefix_ok(n1, n3))
    check("不同省（云南 vs 西藏）即使尾部相同也不满足行政区前缀",
          not pipeline._address_prefix_ok(norm(ADDR_TIBET_1),
                                          norm(ADDR_TIBET_OTHER)))
    check("跨省高相似地址不满足 ADDRESS 判据",
          not pipeline._consolidate_pair(norm(ADDR_TIBET_1),
                                         norm(ADDR_TIBET_OTHER), "ADDRESS"))
    # doc07 同类：一方缺省级前缀（「西藏日喀则…」vs「日喀则…」）→ 单边归一后同行政区
    n_np = norm(ADDR_TIBET_NO_PROV)
    check("单边缺省级前缀（日喀则… vs 西藏日喀则…）→ 同行政区",
          pipeline._address_prefix_ok(n1, n_np))
    check("单边缺省级前缀 → ADDRESS 判据近似",
          pipeline._consolidate_pair(n1, n_np, "ADDRESS"))
    check("双方都无省级前缀但城市不同 → 不误并",
          not pipeline._address_prefix_ok(norm("成都市武侯区人民南路一号"),
                                          norm("重庆市武侯区人民南路一号")))

    ents = [_ent("ADDRESS", ADDR_TIBET_1, "假地址甲"),
            _ent("ADDRESS", ADDR_TIBET_2, "假地址乙"),
            _ent("ADDRESS", ADDR_TIBET_3, "假地址丙")]
    session = pipeline.Session()
    stats = pipeline.consolidate_surrogates(ents, {}, session)
    check("三段式地址变体并为一簇", stats["groups"] == 1
          and stats["members"] == 3, str(stats))
    check("统一为最长原值（含年份）的假值",
          len({e.decision.replacement for e in ents}) == 1
          and ents[0].decision.replacement == "假地址甲",
          str({e.decision.replacement for e in ents}))

    # 跨省不得误并：两个高相似地址各自保持原假值
    ents2 = [_ent("ADDRESS", ADDR_TIBET_1, "假地址A"),
             _ent("ADDRESS", ADDR_TIBET_OTHER, "假地址B")]
    pipeline.consolidate_surrogates(ents2, {}, pipeline.Session())
    check("跨省高相似地址不误并",
          ents2[0].decision.replacement == "假地址A"
          and ents2[1].decision.replacement == "假地址B",
          f"{ents2[0].decision.replacement} / {ents2[1].decision.replacement}")

    # doc07 同类：缺省级前缀的变体并入「西藏日喀则…」，canonical 取更长原值
    ents3 = [_ent("ADDRESS", ADDR_TIBET_NO_PROV, "假地址-短"),
             _ent("ADDRESS", ADDR_TIBET_1, "假地址-长")]
    pipeline.consolidate_surrogates(ents3, {}, pipeline.Session())
    check("缺省级前缀变体并入同一簇（canonical=更长原值）",
          ents3[0].decision.replacement == ents3[1].decision.replacement
          == "假地址-长",
          f"{ents3[0].decision.replacement} / {ents3[1].decision.replacement}")


def test_org_alias(pipeline) -> None:
    print("\n[6] ORG 简称别名（doc03 同类）", flush=True)
    core = pipeline._org_core
    check("core：云基公司 → 云基", core(ORG_ALIAS_SHORT) == "云基",
          core(ORG_ALIAS_SHORT))
    check("core：股份有限公司 后缀被剥掉",
          core(ORG_ALIAS_LONG) == "云基智慧工程", core(ORG_ALIAS_LONG))
    check("core：四川华鑫建材有限公司 → 四川华鑫建材",
          core(ORG_A) == "四川华鑫建材", core(ORG_A))
    pair = pipeline._consolidate_pair
    check("简称 core 是长称 core 的前缀 → 同机构",
          pair(pipeline._consolidate_norm(ORG_ALIAS_SHORT),
               pipeline._consolidate_norm(ORG_ALIAS_LONG), "ORG"))
    check("非前缀 core 不并",
          not pair(pipeline._consolidate_norm(ORG_ALIAS_SHORT),
                   pipeline._consolidate_norm(ORG_ALIAS_OTHER), "ORG"))

    ents = [_ent("ORG", ORG_ALIAS_SHORT, "假值-简称"),
            _ent("ORG", ORG_ALIAS_LONG, "假值-长称")]
    session = pipeline.Session()
    stats = pipeline.consolidate_surrogates(ents, {}, session)
    check("ORG 简称/长称并为一簇", stats["groups"] == 1
          and stats["members"] == 2, str(stats))
    check("统一为最长原值（长称）的假值",
          ents[0].decision.replacement == ents[1].decision.replacement
          == "假值-长称",
          f"{ents[0].decision.replacement} / {ents[1].decision.replacement}")
    check("canonical（长称）映射键排最前",
          next(iter(session.map)) == ("ORG", ORG_ALIAS_LONG),
          str(next(iter(session.map))))


def test_org_stopwords(pipeline) -> None:
    print("\n[7] ORG 误检词禁止归并（doc06/07 误报不得并入真实机构）", flush=True)
    cases = [
        ("通知", "通知有限公司"),
        ("公章", "公章管理有限公司"),
        ("业主", "业主委员会"),
        ("管理", "管理咨询有限公司"),      # 误报 core 是真实机构 core 的前缀
        ("按照", "按照中心"),
        ("方有权", "方有权银行"),
    ]
    for short, long in cases:
        ents = [_ent("ORG", short, "假值-误报"),
                _ent("ORG", long, "假值-真实")]
        pipeline.consolidate_surrogates(ents, {}, pipeline.Session())
        ok = (ents[0].decision.replacement == "假值-误报"
              and ents[1].decision.replacement == "假值-真实")
        check(f"误检词不并入真实机构：{short} / {long}", ok,
              f"{ents[0].decision.replacement} / {ents[1].decision.replacement}")

    # 误检词与真实机构混排时，真实机构之间的别名归并仍然生效
    ents = [_ent("ORG", "通知", "假值-误报"),
            _ent("ORG", ORG_ALIAS_SHORT, "假值-简称"),
            _ent("ORG", ORG_ALIAS_LONG, "假值-长称")]
    pipeline.consolidate_surrogates(ents, {}, pipeline.Session())
    check("混排：真实机构简称/长称仍归并，误报保持独立",
          ents[1].decision.replacement == ents[2].decision.replacement == "假值-长称"
          and ents[0].decision.replacement == "假值-误报",
          f"{ents[0].decision.replacement} / {ents[1].decision.replacement}")


def test_no_overmerge_and_untouched(pipeline) -> None:
    print("\n[8] 不误并 / black、keep、rejected、非 surrogate 不动", flush=True)
    ents = [
        _ent("ORG", ORG_A, "假值-A"),
        _ent("ORG", ORG_B, "假值-B"),
        # 同文本不同类型：不并
        _ent("ADDRESS", ORG_A, "假地址"),
        # 不同类型 + 完全不同值
        _ent("PERSON", "张三丰", "假人名"),
        # AMOUNT 明确不归并
        _ent("AMOUNT", "12345.67", "99999.99"),
        _ent("AMOUNT", "12345.67元", "88888.88"),
        # black / keep / rejected / 非 surrogate：一律不动
        _ent("ORG", ORG_A, None, strategy="black", black=True),
        _ent("ORG", ORG_B, ORG_B, strategy="keep"),
        _ent("ORG", ORG_A, "假值-被拒", status="rejected"),
        _ent("ORG", ORG_B, "假值-mask", strategy="mask"),
    ]
    session = pipeline.Session()
    pipeline.consolidate_surrogates(ents, {}, session)

    check("不同类型同文本不并", ents[2].decision.replacement == "假地址",
          str(ents[2].decision.replacement))
    check("PERSON 不跨类型并", ents[3].decision.replacement == "假人名",
          str(ents[3].decision.replacement))
    check("AMOUNT 不归并",
          ents[4].decision.replacement == "99999.99"
          and ents[5].decision.replacement == "88888.88",
          f"{ents[4].decision.replacement} / {ents[5].decision.replacement}")
    check("AMOUNT 不写入 session.map",
          not any(k[0] == "AMOUNT" for k in session.map), str(list(session.map)))
    check("black 决策不变",
          ents[6].decision.black is True and ents[6].decision.replacement is None,
          f"{ents[6].decision.strategy}/{ents[6].decision.black}")
    check("keep 决策不变", ents[7].decision.strategy == "keep"
          and ents[7].decision.replacement == ORG_B,
          f"{ents[7].decision.strategy}/{ents[7].decision.replacement}")
    check("rejected 决策不变", ents[8].status == "rejected"
          and ents[8].decision.replacement == "假值-被拒",
          f"{ents[8].status}/{ents[8].decision.replacement}")
    check("非 surrogate 决策不变", ents[9].decision.strategy == "mask"
          and ents[9].decision.replacement == "假值-mask",
          f"{ents[9].decision.strategy}/{ents[9].decision.replacement}")
    check("被跳过实体不进 session.map",
          ("ORG", ORG_A) not in session.map or session.map.get(("ORG", ORG_A)) != "假值-被拒")


def test_idempotent(pipeline) -> None:
    print("\n[9] 幂等（重复调用不再改变决策 / 映射）", flush=True)
    ents = [_ent("ORG", ORG_A, "假值甲"),
            _ent("ORG", ORG_B, "假值乙"),
            _ent("ORG", ORG_LONG, "假值丙"),
            _ent("ADDRESS", ADDR_A, "假地址甲"),
            _ent("ADDRESS", ADDR_B, "假地址乙")]
    session = pipeline.Session()
    first = pipeline.consolidate_surrogates(ents, {}, session)
    snap_dec, snap_map = _snapshot(ents, session)
    second = pipeline.consolidate_surrogates(ents, {}, session)
    check("幂等：第二次调用 groups 相同、changed=0",
          first["groups"] == second["groups"] and second["changed"] == 0,
          f"first={first} second={second}")
    check("幂等：决策快照不变", _snapshot(ents, session)[0] == snap_dec)
    check("幂等：session.map 不变", _snapshot(ents, session)[1] == snap_map)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    from yinzhan import pipeline

    print("数据隐站 · Phase A2 近似实体假值一致性自测", flush=True)
    print(f"  临时数据目录：{_TMP}", flush=True)
    test_thresholds(pipeline)
    test_org_merge(pipeline)
    test_canonical(pipeline)
    test_chain(pipeline)
    test_address_variants(pipeline)
    test_org_alias(pipeline)
    test_org_stopwords(pipeline)
    test_no_overmerge_and_untouched(pipeline)
    test_idempotent(pipeline)

    print("\n" + "=" * 66, flush=True)
    total = PASS_COUNT + len(FAILS)
    if FAILS:
        print(f"  FAIL：{len(FAILS)}/{total} 项失败", flush=True)
        for name, detail in FAILS:
            print(f"    ✗ {name}  {detail}", flush=True)
        print("=" * 66, flush=True)
        return 1
    print(f"  PASS：{total} 项全部通过", flush=True)
    print("=" * 66, flush=True)
    return 0


if __name__ == "__main__":
    _code = 2
    try:
        _code = main()
    except Exception:
        traceback.print_exc()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(_code)
