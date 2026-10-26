# -*- coding: utf-8 -*-
"""导出状态污染回归（2026-10-25 QA H-19）。

human 涂黑导出曾**原地改写** `job.entities[*].decision = black`；用户先导涂黑版、
再导「给 AI 看」换名版时，AI 版会沿用 black 退化为涂黑版（文件名仍叫「换名」、
映射库假值在原文件中不存在，rehydrate 失效）。

本测试锁定：同一 Job 先 human 再 ai，ai 成品必须仍是换名版（≠ human 成品，
含换名假值、不含原文），且不影响后续再次导出。
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="yz_export_state_"))
os.environ.setdefault("YINZHAN_DATA_DIR", str(TMP / "data"))
os.environ.setdefault("YINZHAN_OUTPUT_DIR", str(TMP / "out"))

REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / "app" / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

from yinzhan import registry                     # noqa: E402
from yinzhan.jobs import MANAGER                 # noqa: E402
from yinzhan.config import OUTPUT_DIR            # noqa: E402


def _make_pdf(path: Path) -> None:
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    lines = [
        "技术服务合同（虚构样例）",
        "联系人：张伟",
        "联系电话：13800138000",
        "身份证号码：310104198703124510",
        "银行卡号：6222021001123456789",
    ]
    y = 72
    for line in lines:
        page.insert_text((72, y), line, fontname="china-s", fontsize=12)
        y += 24
    doc.save(str(path))
    doc.close()


class ExportStateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        registry.load_plugins()
        cls.pdf = TMP / "export_state.pdf"
        _make_pdf(cls.pdf)
        cls.job = MANAGER.create(cls.pdf, strictness="standard", pack_ids=None,
                                 id_card_mode="fields", time_blur=False, origin="api")
        while cls.job.stage not in ("ready", "failed"):
            time.sleep(0.2)
        assert cls.job.stage == "ready", cls.job.error

    def _export(self, mode: str, tag: str) -> Path:
        out = OUTPUT_DIR / f"export_state_{mode}_{tag}.pdf"
        out.unlink(missing_ok=True)
        MANAGER._export_to(self.job, mode, out, do_verify=True)
        self.assertTrue(out.is_file(), f"{mode}/{tag} 成品不存在")
        return out

    def test_human_then_ai_keeps_rename_version(self):
        human = self._export("human", "1")
        ai = self._export("ai", "1")
        import fitz
        human_text = "".join(page.get_text() for page in fitz.open(str(human)))
        ai_text = "".join(page.get_text() for page in fitz.open(str(ai)))
        original = "13800138000"
        self.assertNotIn(original, human_text, "human 涂黑版应真删除原文文本层")
        self.assertNotIn(original, ai_text, "ai 换名版不得保留原手机号")
        self.assertNotEqual(human.read_bytes(), ai.read_bytes(),
                            "先 human 再 ai：AI 版不得退化为 human 涂黑版")
        self.assertTrue(re.search(r"1[3-9]\d{9}", ai_text),
                        "ai 换名版应含替换后的手机号假值，文本=" + ai_text[:200])
        # 再次 AI 导出仍应稳定（不被上一次导出污染）
        ai2 = self._export("ai", "2")
        self.assertNotEqual(human.read_bytes(), ai2.read_bytes())
        self.assertNotIn(original, "".join(page.get_text()
                                           for page in fitz.open(str(ai2))))

    def test_human_export_does_not_mutate_job_entities(self):
        before = {e.id: (e.decision.strategy if e.decision else None)
                  for e in self.job.entities}
        self._export("human", "3")
        after = {e.id: (e.decision.strategy if e.decision else None)
                 for e in self.job.entities}
        self.assertEqual(before, after, "human 导出不得原地改写 job.entities 的 decision")


if __name__ == "__main__":
    unittest.main(verbosity=2)
