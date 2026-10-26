# -*- coding: utf-8 -*-
"""超长文本保护回归（2026-10-25 QA E-10）。

真实 15MB / 17 万行 TXT 在 NER 规则上跑 >16 分钟、检测阶段无法取消。本测试锁定：

1. `_big_text_policy` 三档阈值：normal / skip_ner（>200 万字符）/ reject（>800 万字符）；
2. `pipeline.detect(skip_types={"ner"})` 确实跳过 NER 类检测并给出降级 warning；
3. 端到端：2.05M 字符文件建任务后 ready，warnings 含「大文本降级」；
   8.1M 字符文件任务置 error（可读提示「文本内容过大」），不是卡死。
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="yz_bigtext_"))
os.environ.setdefault("YINZHAN_DATA_DIR", str(TMP / "data"))
os.environ.setdefault("YINZHAN_OUTPUT_DIR", str(TMP / "out"))

REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / "app" / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

from yinzhan import pipeline, registry        # noqa: E402
from yinzhan.jobs import (                    # noqa: E402
    BIG_TEXT_NER_CHARS, BIG_TEXT_REJECT_CHARS, MANAGER, _big_text_policy,
)
from yinzhan.udm import Block, Document, Page, Rule  # noqa: E402


def _text_doc(text: str) -> Document:
    doc = Document(doc_id="d_big", path="", fmt="text")
    page = Page(index=0, width=595, height=842)
    page.blocks.append(Block(id="b0", kind="text", text=text, bbox=None, page=0))
    doc.pages.append(page)
    return doc


class BigTextPolicyTest(unittest.TestCase):
    def test_thresholds(self):
        self.assertEqual(_big_text_policy(0), "normal")
        self.assertEqual(_big_text_policy(BIG_TEXT_NER_CHARS), "normal")
        self.assertEqual(_big_text_policy(BIG_TEXT_NER_CHARS + 1), "skip_ner")
        self.assertEqual(_big_text_policy(BIG_TEXT_REJECT_CHARS), "skip_ner")
        self.assertEqual(_big_text_policy(BIG_TEXT_REJECT_CHARS + 1), "reject")

    def test_detect_skip_types_warns(self):
        registry.load_plugins()
        doc = _text_doc("联系人：张伟，电话 13800138000")
        rule = Rule(id="t.ner", entity="PERSON", label="人名", level="L2",
                    scope=["text"],
                    detect=[{"type": "ner", "types": ["person"]}],
                    replace={"strategy": "surrogate"})
        det = pipeline.detect(doc, [rule], "standard", skip_types={"ner"})
        self.assertTrue(any("大文本降级" in w for w in det.warnings),
                        f"跳过 NER 必须有降级 warning：{det.warnings}")
        self.assertFalse(any(e.etype == "PERSON" for e in det.entities))

    def _make_txt(self, name: str, chars: int) -> Path:
        path = TMP / name
        # 单行（text parser 逐行建 block，单行也走同一保护逻辑）
        path.write_text("当事人" * max(1, chars // 3), encoding="utf-8")
        return path

    def test_job_skip_ner_for_big_text(self):
        # 用 mock 把阈值调小，避免测试真的处理 200 万字符（端到端只验证接线）
        import yinzhan.jobs as jobs_mod
        from unittest import mock
        path = self._make_txt("big_small.txt", 1200)
        with mock.patch.object(jobs_mod, "BIG_TEXT_NER_CHARS", 1000), \
                mock.patch.object(jobs_mod, "BIG_TEXT_REJECT_CHARS", 100000):
            job = MANAGER.create(path, strictness="standard", pack_ids=None,
                                 id_card_mode="fields", time_blur=False, origin="api")
            deadline = time.time() + 120
            while job.stage not in ("ready", "failed", "error") and time.time() < deadline:
                time.sleep(0.2)
        self.assertEqual(job.stage, "ready", job.error)
        self.assertTrue(any("大文本降级" in w for w in job.warnings),
                        f"任务 warnings 应有降级提示：{job.warnings[:5]}")

    def test_job_rejects_huge_text(self):
        import yinzhan.jobs as jobs_mod
        from unittest import mock
        path = self._make_txt("huge_small.txt", 1200)
        with mock.patch.object(jobs_mod, "BIG_TEXT_NER_CHARS", 1000), \
                mock.patch.object(jobs_mod, "BIG_TEXT_REJECT_CHARS", 1100):
            job = MANAGER.create(path, strictness="standard", pack_ids=None,
                                 id_card_mode="fields", time_blur=False, origin="api")
            deadline = time.time() + 120
            while job.stage not in ("ready", "failed", "error") and time.time() < deadline:
                time.sleep(0.2)
        self.assertEqual(job.stage, "error", "超大文本应直接报可读错误，不能卡死")
        self.assertIn("文本内容过大", job.error or "")


if __name__ == "__main__":
    unittest.main(verbosity=2)

