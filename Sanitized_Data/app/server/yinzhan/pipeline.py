"""处理管线：Parse -> Detect -> Plan -> Apply(export) -> Verify。

这是整个引擎的中枢。所有格式差异都被 UDM 和 Parser 协议挡在外面，
所以这里只写一遍逻辑。
"""
from __future__ import annotations

import re
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from . import registry
from .config import LEVEL_RANK, STRICTNESS
from .ocr import get_ocr
from .udm import (
    Block, Decision, DetectContext, Document, Entity, ExportResult, ExportError,
    Page, ParseContext, ParseError, ReplaceContext, Rule, Span, VerifyError,
    union_boxes, YinzhanError,
)

ORIGIN_RANK = {"manual": 4, "regex": 3, "dict": 2, "ocr": 2, "model": 1}


# ══════════════════════════════ 检测 ══════════════════════════════

@dataclass
class Detection:
    entities: list[Entity] = field(default_factory=list)
    rules: dict[str, Rule] = field(default_factory=dict)   # entity_id -> rule
    warnings: list[str] = field(default_factory=list)
    elapsed_ms: int = 0


def _detector_for(spec: dict, cache: dict):
    kind = spec.get("type")
    cls = registry.all_detectors().get(kind)
    if cls is None:
        # 允许 type 直接写检测器 id
        cls = registry.all_detectors().get(kind or "")
    if cls is None:
        return None
    key = (kind, id(spec))
    if key not in cache:
        try:
            cache[key] = cls.from_spec(spec)
        except Exception:
            cache[key] = None
    return cache[key]


def detect(doc: Document, rules: list[Rule], strictness: str = "standard") -> Detection:
    import time
    t0 = time.time()
    conf = STRICTNESS.get(strictness, STRICTNESS["standard"])
    ctx = DetectContext(strictness=strictness, ocr=get_ocr())
    cache: dict = {}
    found: list[Entity] = []
    rule_of: dict[str, Rule] = {}
    warnings: list[str] = []

    for rule in rules:
        if not rule.enabled:
            continue
        if doc.fmt not in rule.scope and "*" not in rule.scope:
            continue
        for spec in rule.detect or []:
            det = _detector_for(spec, cache)
            if det is None:
                warnings.append(f"规则 {rule.id} 的检测器 {spec.get('type')} 未注册")
                continue
            try:
                ents = det.detect(doc, rule, ctx)
            except Exception as exc:
                warnings.append(f"规则 {rule.id} 检测失败：{exc}")
                continue
            for e in ents:
                if not e.value:
                    continue
                e.etype = e.etype or rule.entity
                e.label = e.label or rule.label
                # 同一实体在多条规则命中时取更严格的密级
                if rule.level and LEVEL_RANK.get(rule.level, 2) > LEVEL_RANK.get(e.level, 2):
                    e.level = rule.level
                found.append(e)
                rule_of[e.id] = rule

    merged = _merge(found)
    # 单字人名是误报（比如把「甲方」尾巴上的「方」当成姓），而且没法复检 —— 直接丢
    merged = [e for e in merged
              if not (e.etype == "PERSON" and len(e.value.strip()) < 2)]
    # 置信度闸门
    kept: list[Entity] = []
    for e in merged:
        if e.confidence < conf["min_conf"] and e.origin != "manual":
            continue
        if e.confidence < conf["pending_below"] and e.origin != "manual":
            e.status = "pending"
        kept.append(e)

    kept.sort(key=lambda e: (e.level, -e.confidence), reverse=False)
    det = Detection(entities=kept, rules=rule_of, warnings=warnings)
    det.elapsed_ms = int((time.time() - t0) * 1000)
    return det


def _merge(entities: list[Entity]) -> list[Entity]:
    """同值合并 + 同位置去重 + 重叠裁剪。"""
    by_key: dict[tuple[str, str], Entity] = {}
    for e in entities:
        key = (e.etype, e.value)
        cur = by_key.get(key)
        if cur is None:
            by_key[key] = e
            continue
        # 合并 spans（去重）
        existing = {(s.block_id, s.start, s.end, s.rect) for s in cur.spans}
        for s in e.spans:
            if (s.block_id, s.start, s.end, s.rect) not in existing:
                cur.spans.append(s)
        cur.confidence = max(cur.confidence, e.confidence)
        if ORIGIN_RANK.get(e.origin, 0) > ORIGIN_RANK.get(cur.origin, 0):
            cur.origin = e.origin
        if LEVEL_RANK.get(e.level, 2) > LEVEL_RANK.get(cur.level, 2):
            cur.level = e.level
        if e.status == "pending":
            cur.status = "pending"

    # 同一个区间被不同类型命中：保留更长的值 / 更高密级
    flat: list[Entity] = list(by_key.values())
    occupied: list[tuple[str, int, int, Entity]] = []
    for e in sorted(flat, key=lambda x: (-len(x.value), -LEVEL_RANK.get(x.level, 0))):
        for s in list(e.spans):
            if not s.is_text:
                occupied.append((s.block_id or "", s.start, s.end, e))
                continue
            clash = _overlaps(occupied, s.block_id or "", s.start, s.end)
            if clash:
                s.start, s.end = -1, -1
                continue
            occupied.append((s.block_id or "", s.start, s.end, e))
        e.spans = [s for s in e.spans if s.is_text or s.rect]
        if not e.spans:
            by_key.pop((e.etype, e.value), None)
    return [e for e in by_key.values() if e.spans]


def _overlaps(occupied, block_id: str, start: int, end: int) -> bool:
    for bid, s, en, _ in occupied:
        if bid != block_id or s < 0:
            continue
        if start < en and s < end:
            return True
    return False


# ══════════════════════════════ 决策 ══════════════════════════════

class Session:
    """会话级实体图：保证同一实体在全文档映射到同一个假值。"""

    def __init__(self) -> None:
        self.map: dict[tuple[str, str], str] = {}
        self.used: dict[str, set[str]] = {}

    def to_dict(self) -> dict:
        out: dict[str, dict[str, str]] = {}
        for (etype, value), repl in self.map.items():
            out.setdefault(etype, {})[value] = repl
        return out

    def mark_used(self, etype: str, repl: str) -> None:
        self.used.setdefault(etype, set()).add(repl)

    def is_used(self, etype: str, repl: str) -> bool:
        return repl in self.used.get(etype, set())


def decide(entities: list[Entity], rules: dict[str, Rule], session: Session) -> None:
    """为每个实体算出 Decision（替换值 / 涂黑）。"""
    for e in entities:
        if e.status == "rejected":
            e.decision = Decision(strategy="keep", replacement=e.value)
            continue
        if e.decision is not None and e.decision.replacement is not None:
            continue  # 用户手工指定过
        rule = rules.get(e.id)
        spec = dict(rule.replace) if rule else {"strategy": "black"}
        if e.etype in ("SECRET", "PRIVATE_KEY", "PASSWORD"):
            spec = {"strategy": "black"}
        key = (e.etype, e.value)
        if key in session.map and spec.get("strategy") == "surrogate":
            e.decision = Decision(strategy="surrogate", replacement=session.map[key])
            continue
        strategy = spec.get("strategy", "mask")
        ctx = ReplaceContext(session=session, etype=e.etype, label=e.label, seed=len(session.map))
        try:
            decision = _build_replacement(strategy, rule, e, ctx)
        except Exception:
            decision = Decision(strategy="black", black=True, replacement=None)
        if strategy == "surrogate" and decision.replacement:
            session.map[key] = decision.replacement
            session.mark_used(e.etype, decision.replacement)
        e.decision = decision


def _build_replacement(strategy: str, rule: Rule | None, e: Entity, ctx: ReplaceContext) -> Decision:
    from .replacers import black as black_mod
    from .replacers import mask as mask_mod
    from .replacers import surrogate as sur_mod

    fake_rule = rule or Rule(id="fallback", entity=e.etype, label=e.label, level=e.level,
                             scope=["*"], detect=[], replace={})
    if strategy == "black":
        return black_mod.build(fake_rule, e.value, ctx)
    if strategy == "drop":
        return Decision(strategy="drop", replacement="")
    if strategy == "keep":
        return Decision(strategy="keep", replacement=e.value)
    if strategy == "surrogate":
        return sur_mod.build(fake_rule, e.value, ctx)
    return mask_mod.build(fake_rule, e.value, ctx)


def decisions_of(entities: list[Entity]) -> dict[str, Decision]:
    return {e.id: (e.decision or Decision(strategy="black", black=True)) for e in entities}


# ══════════════════════════════ 值级传播 ══════════════════════════════

def propagate_values(doc: Document, entities: list[Entity],
                     min_len: int = 6, min_prefix: int = 8) -> int:
    """把已经识别出的实体值，在全文其它位置也补上。

    为什么需要：OCR 会把同一行切成几段（公章压字、扫描歪斜尤其明显），
    同一个公司名在签字页可能只被认出前 13 个字，规则就匹配不到了。
    完整值匹配不到时退一步用前缀匹配 —— 只对足够长的值这么做，避免误伤。
    """
    blocks = [(b.id, b.text) for page in doc.pages for b in page.blocks
              if b.kind == "text" and b.text]
    added = 0
    for e in entities:
        # 人名只有两三个字，但也得扩散（同一个名字在正文里还会出现），
        # 只是不允许前缀匹配，免得「张伟」把「张伟明」也吃掉。
        floor = 2 if e.etype == "PERSON" else min_len
        if e.status == "rejected" or not e.value or len(e.value) < floor:
            continue
        covered = {(s.block_id, s.start, s.end) for s in e.spans if s.block_id}
        for bid, text in blocks:
            pos, match_len = -1, 0
            p = text.find(e.value)
            if p >= 0:
                pos, match_len = p, len(e.value)
            elif len(e.value) >= min_prefix:
                for length in range(len(e.value) - 1, min_prefix - 1, -1):
                    q = text.find(e.value[:length])
                    if q >= 0:
                        pos, match_len = q, length
                        break
            if pos < 0:
                continue
            key = (bid, pos, pos + match_len)
            if key in covered:
                continue
            if _span_taken(entities, bid, pos, pos + match_len, exclude=e.id):
                continue
            e.spans.append(Span(block_id=bid, start=pos, end=pos + match_len))
            covered.add(key)
            added += 1
    return added


def _span_taken(entities: list[Entity], block_id: str, start: int, end: int,
                exclude: str = "") -> bool:
    for e in entities:
        if e.id == exclude or e.status == "rejected":
            continue
        for s in e.spans:
            if s.block_id == block_id and s.start >= 0 and s.start < end and start < s.end:
                return True
    return False


# ══════════════════════════════ 表单填空里的人名 ══════════════════════════════

# 扫描合同里常见这种排版：
#     委托方（以下简称甲方）：        包罗
# 标签和名字被 OCR 切成两个 block，NER 靠"附近有提示词"就瞎了。
# 这里按「同一行 + 在标签右边」的几何关系把它捞回来。
_FORM_LABELS = ("委托方", "委托人", "甲方", "乙方", "当事人", "代理人",
                "代表人", "姓名", "联系人", "经办人", "签署人", "申请人",
                "被申请人", "原告", "被告", "承租人", "出租人")
_FORM_RULE = Rule(id="cn.form_name", entity="PERSON", label="人名", level="L2",
                  scope=["*"], detect=[], replace={"strategy": "surrogate"})


def _is_cjk_name(text: str) -> bool:
    text = text.strip()
    if not (2 <= len(text) <= 4):
        return False
    if any(ch in text for ch in "：:，。、（）()·-—_/\\ 　"):
        return False
    return all("\u4e00" <= ch <= "\u9fa5" for ch in text)


def _same_line(a, b) -> bool:
    ca = (a[1] + a[3]) / 2.0
    cb = (b[1] + b[3]) / 2.0
    return abs(ca - cb) <= max(10.0, min(a[3] - a[1], b[3] - b[1]) * 0.7)


_NAME_STOP = frozenset({
    "委托", "代理", "指派", "聘请", "本所", "该所", "上述", "全体", "两名",
    "三名", "一名", "辩护", "兼职", "专职", "各位", "律师", "对方", "各方",
    "甲方", "乙方", "丙方", "第三", "当事", "其委", "本合", "同约", "约定",
})
# 胡连林、蒋港律师  —— 两个名字并列，最后一个带「律师」
# 常见姓氏：并列结构里用它把「照法律、法规及律师……」这种句子挡掉
_SURNAMES = frozenset(
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
    "戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳酆鲍史唐"
    "费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟平黄"
    "和穆萧尹姚邵湛汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁"
    "杜阮蓝闵席季麻强贾路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍"
    "虞万支柯昝管卢莫房裘缪干解应宗丁宣贲邓郁单杭洪包诸左石崔吉钮龚程"
    "嵇邢滑裴陆荣翁荀羊於惠甄曲家封芮羿储靳汲邴糜松井段富巫乌焦巴弓牧"
    "隗山谷车侯宓蓬全郗班仰秋仲伊宫宁仇栾暴甘钭厉戎祖武符刘景詹束龙叶"
    "幸司韶郜黎蓟薄印宿白怀蒲邰从鄂索咸籍赖卓蔺屠蒙池乔阴郁胥能苍双闻"
    "莘党翟谭贡劳逄姬申扶堵冉宰郦雍郤璩桑桂濮牛寿通边扈燕冀郏浦尚农温"
    "别庄晏柴瞿阎充慕连茹习宦艾鱼容向古易慎戈廖庾终暨居衡步都耿满弘匡"
    "国文寇广禄阙东欧殳沃利蔚越夔隆师巩厍聂晁勾敖融冷訾辛阚那简饶空曾"
    "毋沙乜养鞠须丰巢关蒯相查后荆红游竺权逯盖益桓公"
)

_LAWYER_PAIR_RE = re.compile(
    r"([\u4e00-\u9fa5]{2,3})、([\u4e00-\u9fa5]{2,3})律师")
# 包罗与杨尚坤、绵阳力嘉……  —— 与/和/及 后面跟一个名字再接顿号
_PARTY_JOIN_RE = re.compile(r"[与和及]([\u4e00-\u9fa5]{2,3})、")


def find_party_names(doc: Document, entities: list[Entity] | None = None) -> list[Entity]:
    """补「A、B律师」这类并列的人名。

    NER 的上下文启发式对这种并列结构不敏感，但合同里几乎每份都有。
    只认「…、…律师」这个强模式：冒号顿号满地都是，别的模式容易误伤。
    """
    existing = entities or []
    out: list[Entity] = []

    def push(bid: str, text: str, start: int, end: int) -> None:
        name = text[start:end]
        if name in _NAME_STOP or not (2 <= len(name) <= 4):
            return
        # 并列结构很容易误伤（「法律、法规及律师」），必须首字是姓氏
        if name[0] not in _SURNAMES:
            return
        occ = _overlapping(existing, bid, start, end) or _overlapping(out, bid, start, end)
        if occ is not None:
            # 旧的是新名字的一部分（例如把「胡连林」认成「连林」），留更长的那个
            if occ.value and occ.value in name and len(occ.value) < len(name):
                occ.status = "rejected"
            elif name in (occ.value or ""):
                return
            else:
                return
        out.append(Entity(
            id=Entity.new_id(), etype="PERSON", label="人名", value=name,
            spans=[Span(block_id=bid, start=start, end=end)],
            level="L2", confidence=0.88, origin="form",
        ))

    for page in doc.pages:
        for b in page.blocks:
            text = b.text or ""
            if not text or len(text) < 4:
                continue
            for m in _LAWYER_PAIR_RE.finditer(text):
                push(b.id, text, m.start(1), m.end(1))
                push(b.id, text, m.start(2), m.end(2))
            # 「包罗与杨尚坤、绵阳力嘉……」：与/和/及 后面跟一个名字再接顿号
            # 有姓氏表把关，不会把「与甲方、」「及法规、」当成名字
            for m in _PARTY_JOIN_RE.finditer(text):
                push(b.id, text, m.start(1), m.end(1))
    return out


def _overlapping(entities: list[Entity], block_id: str, start: int, end: int):
    for e in entities:
        if e.status == "rejected":
            continue
        for s in e.spans:
            if s.block_id == block_id and s.start >= 0 and s.start < end and start < s.end:
                return e
    return None


def find_form_names(doc: Document, entities: list[Entity] | None = None) -> list[Entity]:
    """找「标签：  张三」这种表单填空里的人名。"""
    existing = entities or []
    out: list[Entity] = []
    for page in doc.pages:
        labels = []
        for b in page.blocks:
            text = (b.text or "").strip()
            if not text or not b.bbox:
                continue
            if not any(k in text for k in _FORM_LABELS):
                continue
            if ":" in text or "：" in text or text.endswith("方"):
                labels.append(b)
        if not labels:
            continue
        for b in page.blocks:
            text = (b.text or "").strip()
            if not b.bbox or not _is_cjk_name(text):
                continue
            if _span_taken(existing, b.id, 0, len(b.text or "")):
                continue
            if _span_taken(out, b.id, 0, len(b.text or "")):
                continue
            for lb in labels:
                # 名字要在标签右边、且同一行
                if _same_line(lb.bbox, b.bbox) and b.bbox[0] >= lb.bbox[2] - 8:
                    out.append(Entity(
                        id=Entity.new_id(), etype="PERSON", label="人名", value=text,
                        spans=[Span(block_id=b.id, start=0, end=len(b.text or ""))],
                        level="L2", confidence=0.9, origin="form",
                    ))
                    break
    return out


# ══════════════════════════════ 签署区 ══════════════════════════════

# 合同落款那几行：单位名称、盖章、代表人、日期。
# 逐个元素抠很容易漏（OCR 被印章压碎、手写认不出），
# 而这块区域本来就不需要给 AI 看，整块盖掉最稳。
_SIG_LABELS = ("盖章", "代表人", "签字", "签名", "签署", "单位名称")
_SIG_STRONG = ("盖章", "单位名称")
_SIG_DATE = "日期"
_SIG_LABEL_MAXLEN = 22   # 落款标签很短；正文里提「签字盖章」的句子很长，用它区分


def find_signature_blocks(doc: Document) -> list[tuple[int, tuple[float, float, float, float]]]:
    """找出一页里的「落款签署区」，返回 (页号, 矩形) 列表。"""
    out: list[tuple[int, tuple[float, float, float, float]]] = []
    for page in doc.pages:
        hits: list[tuple[float, float, float, float]] = []
        dates: list[tuple[float, float, float, float]] = []
        has_strong = False
        for b in page.blocks:
            text = (b.text or "").replace(" ", "").replace("\u3000", "")
            if not text or not b.bbox or len(text) > _SIG_LABEL_MAXLEN:
                continue
            if any(k in text for k in _SIG_LABELS):
                hits.append(b.bbox)
                if any(k in text for k in _SIG_STRONG):
                    has_strong = True
            elif _SIG_DATE in text and len(text) <= 14:
                dates.append(b.bbox)
        # 至少有一处「盖章/单位名称」、且同类线索不少于 2 条，才算落款区；
        # 正文里随便一个「日期」不会被当成签署区。
        if not has_strong or len(hits) < 2:
            continue
        # 日期行紧跟在落款下面，只有确认是落款区时才并进来
        hits.extend(dates)
        x0 = min(h[0] for h in hits)
        y0 = min(h[1] for h in hits)
        x1 = max(h[2] for h in hits)
        y1 = max(h[3] for h in hits)
        pad = 5.0
        # 落款区横向铺到正文栏宽：手写签名常写在标签右边、印章盖在名字上，
        # 只按标签自己的框裁会漏掉半截。
        left = min(min(h[0] for h in hits) - pad, page.width * 0.06)
        right = max(max(h[2] for h in hits) + pad, page.width * 0.94)
        # 手写日期/签名常常写在印好的「年 月 日」下面一行，落款区往下多兜一截
        bottom_pad = min(90.0, page.height * 0.10)
        rect = (max(0.0, left), max(0.0, y0 - pad),
                min(page.width, right), min(page.height, y1 + bottom_pad))
        if rect[2] - rect[0] < 40 or rect[3] - rect[1] < 20:
            continue
        out.append((page.index, rect))
    return out


# ══════════════════════════════ 导出 ══════════════════════════════

def export(doc: Document, parser: registry.Parser, entities: list[Entity],
           out_path: Path, options: dict) -> ExportResult:
    active = [e for e in entities if e.status != "rejected" and e.decision]
    decisions = decisions_of(active)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result = parser.export(doc, active, decisions, out_path, options)
    if not result or not Path(result.path).exists():
        raise ExportError("导出没有产出文件")
    result.size = Path(result.path).stat().st_size
    return result


# ══════════════════════════════ 出口自检 ══════════════════════════════

META_KEYS = {
    "author", "creator", "producer", "subject", "keywords", "title",
    "creationdate", "moddate", "last_modified_by", "company", "manager",
}

GEO_TAGS = {33550, 33922, 34735, 34264, 42112, 42113}

# 视觉/码类实体 -> 重解析成品后「本不该再出现」的 visual_marks kind。
# 只在 P0 新抽出的目标上做残留判定；印章/签名的复检沿用旧逻辑，避免误报。
_RECT_ETYPE_KIND = {
    "FACE": "face",
    "ID_DOCUMENT": "id_document",
    "PLATE": "plate",
    "ROAD_SIGN": "road_sign",
    "DOOR_ADDRESS": "door_address",
    "QRCODE": "qrcode",
    "BARCODE": "barcode",
}

_VISUAL_IOU = 0.3


def _iou_box(a, b) -> float:
    """两个矩形的交并比。"""
    try:
        x0, y0 = max(a[0], b[0]), max(a[1], b[1])
        x1, y1 = min(a[2], b[2]), min(a[3], b[3])
        if x1 <= x0 or y1 <= y0:
            return 0.0
        inter = (x1 - x0) * (y1 - y0)
        area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
        area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
        union = area_a + area_b - inter
        return inter / union if union > 0 else 0.0
    except Exception:
        return 0.0


def _visual_residual(re_doc: Document, entities: list[Entity]) -> list[str]:
    """重解析成品后仍能扫到同类视觉/码类目标 = 没涂干净。

    坐标系：实体 span.rect 与 visual_marks.rect 都是各自解析器输出的页面坐标
    （PDF 为 pt、图片/TIFF 为 px），所以可以直接比。
    """
    found: dict[str, list] = {}
    for mark in getattr(re_doc, "visual_marks", None) or []:
        found.setdefault(getattr(mark, "kind", ""), []).append(mark)
    if not found:
        return []
    bad: list[str] = []
    for entity in entities:
        if entity.status == "rejected":
            continue
        kind = _RECT_ETYPE_KIND.get(entity.etype)
        if not kind:
            continue
        marks = found.get(kind) or []
        if not marks:
            continue
        for span in entity.spans or []:
            if not span.rect:
                continue
            page = span.page if span.page is not None else 0
            for mark in marks:
                if getattr(mark, "page", 0) != page:
                    continue
                if _iou_box(span.rect, mark.rect) >= _VISUAL_IOU:
                    bad.append(f"{entity.label}@{page}")
                    break
            else:
                continue
            break
    return bad


def has_cleanup_targets(doc: Document) -> bool:
    """没有实体时，文件里是否仍有「必须清理才算交付」的内容。

    典型场景：正射影像只有 Geo 标签、没有可 OCR 的文字；以前这种文件会卡在
    「没有需要隐去的内容」无法导出（详见 docs/red-lines.md 扩展点 E2）。
    """
    meta = getattr(doc, "meta", None) or {}
    if meta.get("geo_tags"):
        return True
    if (getattr(doc, "extra", None) or {}).get("geo_transform"):
        return True
    if (getattr(doc, "extra", None) or {}).get("geo_params"):
        return True
    for key, value in meta.items():
        try:
            if value and str(key).lower() in META_KEYS:
                return True
        except Exception:
            continue
    return False


def _replacement_intervals(text: str, entities: list[Entity]) -> list[tuple[int, int]]:
    """成品文本里所有保形假值占据的区间（用于排除假值内部子串误报）。"""
    intervals: list[tuple[int, int]] = []
    seen: set[str] = set()
    for e in entities:
        if e.status == "rejected" or e.decision is None:
            continue
        dec = e.decision
        if dec.black or dec.strategy in ("black", "drop", "keep"):
            continue
        repl = (dec.replacement or "").strip()
        if len(repl) < 2 or repl in seen:
            continue
        seen.add(repl)
        start = 0
        while True:
            pos = text.find(repl, start)
            if pos < 0:
                break
            intervals.append((pos, pos + len(repl)))
            start = pos + 1
    return intervals


def verify(exported: Path, entities: list[Entity], parser: registry.Parser,
           ctx: ParseContext, *, doc: Document | None = None,
           options: dict | None = None) -> dict:
    """对成品再跑一遍：原文是否还在、元数据是否干净、视觉/码类是否清干净。

    :param doc: 原始文档（可选）。用于检查「未处理的内嵌媒体」等导出前信息。
    :param options: 导出选项，``allow_unprocessed_media=True`` 表示用户已二次
        确认要导出含未脱敏内嵌图片的 Office 文件（见 docs/red-lines.md E3）。
    """
    issues: list[str] = []
    residual: list[str] = []
    checked = 0
    options = dict(options or {})

    try:
        re_doc = parser.parse(exported, ctx)
    except Exception as exc:
        return {"verified": False, "residual": None, "issues": [f"成品无法重新解析：{exc}"]}

    text = _all_text(re_doc)
    # 换名版会把保形假值写回成品；假值里可能保留"人民法院/省市区"这类不敏感
    # 共同片段，导致原文值作为子串出现在**假值内部**。先记下所有假值在成品文本
    # 里的覆盖区间，属于假值内部的命中不算泄漏，假值之外还出现才是真残留。
    covered = _replacement_intervals(text, entities)
    for e in entities:
        if e.status == "rejected" or not e.value:
            continue
        # keep = 产品显式保留（如 HS 编码/价格条款，按决策不进脱敏），不是残留
        if e.decision is not None and e.decision.strategy == "keep":
            continue
        checked += 1
        needle = e.value.strip()
        # 单字没法验（「方」在「甲方」里到处都是），只检查两个字符以上的值
        if len(needle) < 2:
            continue
        leaked = False
        start = 0
        while True:
            pos = text.find(needle, start)
            if pos < 0:
                break
            end = pos + len(needle)
            if not any(s <= pos and end <= e2 for s, e2 in covered):
                leaked = True
                break
            start = pos + 1
        if leaked:
            residual.append(e.value)

    if residual:
        issues.append(f"仍有 {len(residual)} 处原文残留")

    # 元数据
    try:
        meta = getattr(re_doc, "meta", {}) or {}
        dirty = [k for k, v in meta.items() if v and k.lower() in META_KEYS]
        if dirty:
            issues.append("元数据未清理：" + ", ".join(dirty[:5]))
        # 图片/TIF 的隐藏元数据：ICC/EXIF/GPS/XMP 残留同样要拦
        extra_dirty = []
        for key, value in meta.items():
            if not value:
                continue
            low = str(key).lower()
            if any(tag in low for tag in ("icc", "exif", "gps", "xmp", "iptc")):
                extra_dirty.append(str(key))
        if extra_dirty:
            issues.append("元数据未清理：" + ", ".join(extra_dirty[:5]))
    except Exception:
        pass

    geo = (getattr(re_doc, "meta", {}) or {}).get("geo_tags")
    if geo:
        issues.append("地理参考标签仍在：" + ", ".join(map(str, geo)))

    # 视觉/码类复检：成品重解析后还能扫到同类目标，说明涂黑没盖住
    visual_bad = _visual_residual(re_doc, entities)
    if visual_bad:
        issues.append(f"仍有 {len(visual_bad)} 处视觉/码类目标残留：" + ", ".join(visual_bad[:8]))

    # 未处理的内嵌媒体：P0 只检测 + 阻断/二次确认
    media_info = {}
    try:
        media_info = dict((getattr(re_doc, "meta", None) or {}).get("embedded_media") or {})
    except Exception:
        media_info = {}
    media_count = int(media_info.get("count") or 0)
    if media_count and not options.get("allow_unprocessed_media"):
        issues.append(f"文件里仍有 {media_count} 张未脱敏的内嵌图片")

    ok = not issues
    if not ok:
        raise VerifyError("；".join(issues))
    return {
        "verified": True,
        "residual": len(residual),
        "checked": checked,
        "visual_residual": len(visual_bad),
        "media": {"count": media_count,
                  "confirmed": bool(options.get("allow_unprocessed_media"))},
        "issues": [],
    }


def _all_text(doc: Document) -> str:
    parts: list[str] = []
    for page in doc.pages:
        for b in page.blocks:
            if b.text:
                parts.append(b.text)
            for c in b.cells:
                if c.text:
                    parts.append(c.text)
    return "\n".join(parts)


# ══════════════════════════════ 预览 ══════════════════════════════

def boxes_for_span(doc: Document, span: Span) -> list[tuple[float, float, float, float]]:
    """把字符区间换算成逐行矩形。"""
    if span.rect:
        return [span.rect]
    if not span.block_id:
        return []
    block = _find_block(doc, span.block_id)
    if block is None:
        return []
    chars = (block.ref or {}).get("chars")
    if chars and span.start >= 0:
        sub = chars[span.start:span.end]
        if sub:
            return _cluster_lines(sub)
    if block.bbox:
        return [block.bbox]
    return []


def _find_block(doc: Document, block_id: str) -> Block | None:
    for page in doc.pages:
        for b in page.blocks:
            if b.id == block_id:
                return b
    return None


def _cluster_lines(boxes: Iterable) -> list[tuple[float, float, float, float]]:
    items = sorted((tuple(map(float, b)) for b in boxes), key=lambda b: (round(b[1], 1), b[0]))
    groups: list[list[tuple]] = []
    for b in items:
        placed = False
        for g in groups:
            gy0 = min(x[1] for x in g)
            gy1 = max(x[3] for x in g)
            mid = (b[1] + b[3]) / 2
            if gy0 - 2 <= mid <= gy1 + 2:
                g.append(b)
                placed = True
                break
        if not placed:
            groups.append([b])
    out = []
    for g in groups:
        xs0 = min(x[0] for x in g)
        ys0 = min(x[1] for x in g)
        xs1 = max(x[2] for x in g)
        ys1 = max(x[3] for x in g)
        out.append((xs0, ys0, xs1, ys1))
    return out


def build_preview(doc: Document, parser: registry.Parser, entities: list[Entity],
                  page_index: int, mode: str) -> dict:
    if page_index < 0 or page_index >= len(doc.pages):
        raise YinzhanError(f"页码越界：{page_index}")
    page = doc.pages[page_index]
    kind = "raster" if getattr(parser, "can_render", False) else "flow"
    blocks = [b.to_dict() for b in page.blocks]
    # 只保留「落在这一页上」的实体。否则多页文档会把第 2 页的框画到第 1 页上。
    # 注意：flow 格式的 block 没有 bbox，不能因为 boxes 为空就把实体丢掉，
    # 所以判据是「这一页上有没有它的 span」，不是「算不算得出矩形」。
    page_block_ids = {b.id for b in page.blocks}
    block_text = {b.id: (b.text or "") for b in page.blocks}
    ents = []
    for e in entities:
        d = e.to_dict()
        view_spans: list[dict] = list(d.get("spans") or [])
        boxes: list[list[float]] = []
        on_this_page = False
        for s in e.spans:
            if s.page is not None and s.page != page_index:
                continue
            if s.block_id is not None and s.block_id not in page_block_ids:
                continue
            on_this_page = True
            for box in boxes_for_span(doc, s):
                boxes.append([round(v, 2) for v in box])
        # 值级扩散：有些块（典型是表格单元格）整块文本恰好等于实体值，
        # 检测器可能没在这里命中，但导出器会按值替换掉。预览要跟导出对齐，
        # 否则用户会在「可外发版」里看见一个没被替换的名字。
        if e.value and len(e.value) >= 2:
            seen_blocks = {sp.get("block_id") for sp in view_spans}
            for bid, txt in block_text.items():
                if bid in seen_blocks or not txt:
                    continue
                pos = txt.find(e.value)
                if pos >= 0 and txt.strip() == e.value.strip():
                    view_spans.append({"block_id": bid, "start": pos, "end": pos + len(e.value)})
                    on_this_page = True
        if not on_this_page:
            continue
        d["spans"] = view_spans
        d["boxes"] = boxes
        d["black"] = bool(e.decision and e.decision.black)
        ents.append(d)
    return {
        "page": page_index,
        "total_pages": len(doc.pages),
        "kind": kind,
        "width": round(page.width, 3),
        "height": round(page.height, 3),
        "blocks": blocks,
        "entities": ents,
    }
