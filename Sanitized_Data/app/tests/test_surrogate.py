# -*- coding: utf-8 -*-
"""R04 回归：地址保形假值不得保留完整原值 + verify 假值豁免收紧。

背景
-----
真实测试集（10 页扫描件合同）曾报 ``verify: 仍有 1 处原文残留``。根因不是误报：
``replacers/surrogate.py::_split_locality()`` 对"整段地址只有省/市/区层级"的值
返回 ``locality == 整段、rest == ""``，``_fake_address()`` 于是把完整原始地址
原样保留在假值开头，成品可见文字与文本层都含原文——这是真实泄漏。

本测试锁定三条不变量（全部为虚构样例）：

1. 地址（含只有省/市/区层级的地址）生成的保形假值**不得包含完整原值**；
2. 任何类型的假值都不得完整包含原值（``_generate`` 兜底不变量）；
3. ``pipeline._replacement_intervals`` 只豁免不包含完整原值的假值；
   假值一旦包含原文，verify 必须把它当残留报出来，不能被"假值内部"豁免掉。

运行（Windows 控制台请带 -X utf8）：

    python -X utf8 app/tests/test_surrogate.py
"""
from __future__ import annotations

import random
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / "app" / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

from yinzhan import pipeline                                  # noqa: E402
from yinzhan.replacers import surrogate                         # noqa: E402
from yinzhan.udm import Decision, Entity, ReplaceContext        # noqa: E402

#: 全部虚构地址；既有"只有省/市/区层级"的整值，也有完整街道地址
ADDRESSES = [
    "成都市武侯区",
    "四川省成都市",
    "北京市朝阳区",
    "新疆维吾尔自治区",
    "内蒙古自治区呼和浩特市",
    "成都市武侯区人民南路四段1号",
    "上海市浦东新区测试路1号2栋3单元401室",
    "重庆市渝北区龙溪街道",
]


def _entity(value: str, replacement: str, etype: str = "ADDRESS") -> Entity:
    ent = Entity(id=Entity.new_id(), etype=etype, label=etype, value=value, spans=[])
    ent.decision = Decision(strategy="surrogate", replacement=replacement)
    return ent


class AddressSurrogateTest(unittest.TestCase):
    def test_locality_only_address_never_keeps_full_value(self):
        for value in ADDRESSES:
            for seed in range(6):
                repl = surrogate._fake_address(value, random.Random(seed), seed)
                self.assertNotEqual(repl, value, f"地址未变化：{value}")
                self.assertNotIn(
                    value, repl,
                    f"假值完整包含原地址（R04 泄漏）：{value} -> {repl}")

    def test_org_annotation_does_not_keep_inner_real_name(self):
        # S2-b（2026-10-25 QA）：括号补充说明本身是真实机构名时，保形假值旧逻辑
        # 会原样保留「（成都市技师学院）」，成品仍可读到真实机构名。
        value = "成都新华永泰技术学院（成都市技师学院）"
        for seed in range(6):
            ctx = ReplaceContext(session={}, etype="ORG", seed=seed)
            repl = surrogate._generate(ctx, "ORG", value, surrogate._FACTORIES["ORG"])
            self.assertNotEqual(repl, value)
            self.assertNotIn(value, repl)
            self.assertNotIn("成都市技师学院", repl,
                             f"括号内真实机构名残留：{repl}")

    def test_org_without_known_locality_not_gibberish(self):
        # S2-d：地名不在词池的政务机构（广汉市…街道办事处）曾退化为随机汉字乱码
        value = "广汉市人民政府雒城街道办事处"
        for seed in range(6):
            ctx = ReplaceContext(session={}, etype="ORG", seed=seed)
            repl = surrogate._generate(ctx, "ORG", value, surrogate._FACTORIES["ORG"])
            self.assertNotEqual(repl, value)
            self.assertNotIn(value, repl)
            self.assertTrue(repl.endswith("办事处"), f"机构类型词丢失：{repl}")
            self.assertIn("广汉市", repl, f"同城感地名应保留：{repl}")

    def test_distinct_orgs_not_merged_by_place_prefix(self):
        # S2-c：不同机构不能仅因共享地名前缀就被合并成同一假值
        ents = [
            _entity("北京大学", "北京立信学院", etype="ORG"),
            _entity("北京物资学院", "北京远航学院", etype="ORG"),
            _entity("四川大学", "四川青禾大学", etype="ORG"),
            _entity("四川师范大学", "四川明理大学", etype="ORG"),
        ]
        stats = pipeline.consolidate_surrogates(ents)
        self.assertEqual(stats["groups"], 0,
                         f"不同机构被误归并：{[(e.value, e.decision.replacement) for e in ents]}")

    def test_org_alias_still_merges(self):
        # 真正的简称别名仍应归并（「云基公司」是「云基智慧工程股份有限公司」的简称）
        ents = [
            _entity("云基公司", "千帆公司", etype="ORG"),
            _entity("云基智慧工程股份有限公司", "千帆智慧工程股份有限公司", etype="ORG"),
        ]
        stats = pipeline.consolidate_surrogates(ents)
        self.assertEqual(stats["groups"], 1)
        self.assertEqual(ents[0].decision.replacement,
                         ents[1].decision.replacement)

    def test_generate_invariant_all_surrogate_types(self):
        cases = {
            "ADDRESS": "成都市武侯区",
            "PERSON": "张伟",
            "ORG": "成都华信科技有限公司",
            "PHONE": "13800138000",
            "ID_CARD": "110101199003074477",
            "EMAIL": "test@example.com",
        }
        for etype, value in cases.items():
            ctx = ReplaceContext(session={}, etype=etype, seed=7)
            factory = surrogate._FACTORIES[etype]
            repl = surrogate._generate(ctx, etype, value, factory)
            self.assertNotEqual(repl, value, f"{etype} 假值未变化")
            self.assertNotIn(value, repl, f"{etype} 假值完整包含原值（泄漏）")
            again = surrogate._generate(ctx, etype, value, factory)
            self.assertEqual(again, repl, f"{etype} 同一会话内假值必须稳定")


class ReplacementIntervalTest(unittest.TestCase):
    def test_safe_fake_is_exempted(self):
        text = "联系人：陈志远，电话 13900139000"
        covered = pipeline._replacement_intervals(
            text, [_entity("张伟", "陈志远", etype="PERSON")])
        self.assertIn("张伟", covered)
        self.assertTrue(covered["张伟"], "安全假值应登记排除区间")

    def test_unsafe_fake_is_not_exempted(self):
        # 假值完整包含原文 → 不登记豁免，verify 的残留检查必须能报出
        value = "成都市武侯区"
        text = "地址：" + value + "永定路88号"
        covered = pipeline._replacement_intervals(text, [_entity(value, text[3:])])
        self.assertEqual(covered.get(value, []), [],
                         "假值完整包含原文时不得豁免（否则真实泄漏会被放过）")

    def test_partial_common_prefix_still_exempted(self):
        # 机构假值保留"人民法院"等共同片段但**不包含完整原值** → 仍应豁免
        value = "成都市武侯区人民法院"
        repl = "成都市朝阳区人民法院"
        text = "审理机关：" + repl
        covered = pipeline._replacement_intervals(
            text, [_entity(value, repl, etype="ORG")])
        self.assertIn(value, covered)
        self.assertTrue(covered[value])

    def test_generic_fragment_inside_other_fake_is_exempted(self):
        # D-01（2026-10-25 真实 29 页判决书）：泛化值「人民法院」落在另一个
        # 机构保形假值「星汉景长中级人民法院」内部时，不得判成原文残留。
        text = "审理机关：星汉景长中级人民法院"
        ents = [
            _entity("人民法院", "亲与人说又", etype="ORG"),
            _entity("四川省成都市中级人民法院", "星汉景长中级人民法院", etype="ORG"),
        ]
        spans = pipeline._safe_replacement_spans(text, ents)
        pos = text.find("人民法院")
        self.assertTrue(spans, "跨实体假值区间应被登记")
        self.assertTrue(any(s <= pos and pos + len("人民法院") <= e2
                            for s, e2 in spans),
                        "泛化片段落在其它假值内部应被豁免（D-01 误报）")

    def test_generic_fragment_outside_fake_still_residual(self):
        # 反向：泛化片段出现在假值之外（真残留）时仍必须能被检出
        text = "本案由人民法院审理；假值：星汉景长中级人民法院"
        ents = [_entity("四川省成都市中级人民法院", "星汉景长中级人民法院", etype="ORG")]
        spans = pipeline._safe_replacement_spans(text, ents)
        pos = text.find("人民法院")
        self.assertFalse(any(s <= pos and pos + len("人民法院") <= e2
                             for s, e2 in spans),
                         "假值之外的命中不得被豁免")

    def test_numeric_fragment_is_not_residual(self):
        # R13 假阳性：金额假值「25万元」里含原金额「5万元」子串 → 属于更长数字 token
        self.assertTrue(pipeline._is_numeric_fragment("共25万元", 2, 5, "5万元"))
        self.assertTrue(pipeline._is_numeric_fragment("金额5000元", 2, 5, "500"))
        self.assertFalse(pipeline._is_numeric_fragment("共5万元", 1, 4, "5万元"))
        self.assertFalse(pipeline._is_numeric_fragment("金额500元", 2, 5, "500"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
