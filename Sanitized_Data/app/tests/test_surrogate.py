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

    def test_numeric_fragment_is_not_residual(self):
        # R13 假阳性：金额假值「25万元」里含原金额「5万元」子串 → 属于更长数字 token
        self.assertTrue(pipeline._is_numeric_fragment("共25万元", 2, 5, "5万元"))
        self.assertTrue(pipeline._is_numeric_fragment("金额5000元", 2, 5, "500"))
        self.assertFalse(pipeline._is_numeric_fragment("共5万元", 1, 4, "5万元"))
        self.assertFalse(pipeline._is_numeric_fragment("金额500元", 2, 5, "500"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
