# -*- coding: utf-8 -*-
"""P2/P3 回归：warning 统一去重 + 异常大面积码类框进入「人工确认」。

对应 QA 报告 ``docs/qa/real-testset-v1.1.0.md``：
* §7.2 warning 噪声：相同 warning 按页重复 64 次（R04/R13/R14 各 10、R17 17…）。
  要求任务层/导出结果层只保留一次并标注出现次数，且**只合并完全相同的字符串**，
  不同错误/失败 warning 不得被合并成一条。
* §7.3 异常大面积码类框：``/detect`` 的 qrcode/barcode 候选面积 >5% 页面的，
  不能静默 ``continue``；生成 status="pending"、decision=None 的待确认实体，
  accept / accept-all 后才补 black 决策，未确认不参与导出。

运行（Windows 控制台需 UTF-8）：

    python -X utf8 app/tests/test_warnings_pending.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# 必须在 import yinzhan.config 之前指向临时目录，避免污染仓库 data/
_TMP = Path(tempfile.mkdtemp(prefix="yz_warn_pending_"))
os.environ.setdefault("YINZHAN_DATA_DIR", str(_TMP / "data"))
os.environ.setdefault("YINZHAN_OUTPUT_DIR", str(_TMP / "out"))

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parents[1]
SERVER = REPO / "app" / "server"
sys.path.insert(0, str(SERVER))

from yinzhan import jobs, pipeline  # noqa: E402
from yinzhan.udm import (  # noqa: E402
    Decision, Document, Entity, ExportResult, Page, Span, VisualMark,
)

BIG_FACE_WARNING = "照片较大，小脸可能漏检，请人工复核/整图涂黑"
LARGE_CODE_WARNING = "检测到疑似二维码/条码但面积异常，未自动打码，已列入待确认列表，请人工确认"


# ────────────────────────────── 小工具 ──────────────────────────────

def _doc(width: float = 1000.0, height: float = 1000.0) -> Document:
    return Document(
        doc_id="d_warn_pending", path="memory", fmt="pdf",
        pages=[Page(index=0, width=width, height=height)],
    )


def _job(doc: Document | None = None,
         entities: list[Entity] | None = None) -> jobs.Job:
    return jobs.Job(
        id="j_warn_pending", name="扫描件.pdf", path="扫描件.pdf", fmt="pdf",
        doc=doc, entities=list(entities or []),
    )


class _CaptureParser:
    """最小 Parser 替身：只记录 pipeline.export 真正传下来的实体。"""

    def __init__(self) -> None:
        self.seen: list[Entity] = []

    def export(self, doc, entities, decisions, out_path, options) -> ExportResult:
        self.seen = list(entities)
        Path(out_path).write_bytes(b"fake-export")
        return ExportResult(path=str(out_path), warnings=[])


# ══════════════════════════ 1. warning 去重 ══════════════════════════

class WarningDedupTest(unittest.TestCase):
    def test_job_warnings_dedupe_with_count_and_order(self) -> None:
        """任务层：相同 warning 只留一条（共 N 次），不同错误各自保留。"""
        job = _job()
        distinct = [
            "规则 cn.id 检测失败：A 校验位不合法",
            "规则 cn.id 检测失败：B 长度不合法",
        ]
        job.warnings = (
            [BIG_FACE_WARNING] * 17
            + distinct
            + [BIG_FACE_WARNING]
            + ["金额一致性处理跳过：ValueError"]
        )
        self.assertEqual(job.warnings.count(BIG_FACE_WARNING), 18)

        # _run 收尾调用的就是这个方法
        job.dedupe_warnings()

        expected = [
            f"{BIG_FACE_WARNING}（共 18 次）",
            distinct[0],
            distinct[1],
            "金额一致性处理跳过：ValueError",
        ]
        self.assertEqual(job.warnings, expected)
        self.assertEqual(job.snapshot()["warnings"], expected)

        # 幂等：再次去重不能把 18 变成 19 或重复叠加后缀
        job.dedupe_warnings()
        self.assertEqual(job.warnings, expected)

    def test_snapshot_dedupes_copy_without_mutating_raw_warnings(self) -> None:
        job = _job()
        job.warnings = ["错误 A", "错误 B", "错误 A"]
        self.assertEqual(job.snapshot()["warnings"],
                         ["错误 A（共 2 次）", "错误 B"])
        # snapshot 是只读快照，不应悄悄改写底层列表
        self.assertEqual(job.warnings, ["错误 A", "错误 B", "错误 A"])

    def test_only_identical_strings_are_merged(self) -> None:
        """前缀/相似但不相等的错误绝不合并（防止掩盖不同错误）。"""
        job = _job()
        job.warnings = ["导出失败：A", "导出失败：A（磁盘已满）", "导出失败：A"]
        job.dedupe_warnings()
        self.assertEqual(job.warnings, [
            "导出失败：A（共 2 次）",
            "导出失败：A（磁盘已满）",
        ])

    def test_export_result_warnings_deduped(self) -> None:
        """导出结果层：parser 导出 warning 同样去重后再回给界面/审计。"""
        mgr = jobs.JobManager()
        job = _job(_doc())
        mgr._jobs[job.id] = job

        warning = "第 1 页与第 2 页出现相同提示"

        def fake_export(doc, parser, entities, out_path, options):
            Path(out_path).write_bytes(b"x")
            return ExportResult(path=str(out_path),
                                warnings=[warning, warning, "另一个提示"])

        with mock.patch.object(jobs.pipeline, "export", fake_export):
            info = mgr._export_to(job, "ai", _TMP / "dedupe_out.bin",
                                  do_verify=False)
        self.assertEqual(info["warnings"],
                         [f"{warning}（共 2 次）", "另一个提示"])


# ═════════════════════ 2. 异常大面积码类框 → 待确认 ═════════════════════

class LargeCodePendingTest(unittest.TestCase):
    def _entities(self, kind: str = "qrcode") -> tuple[Document, list[Entity]]:
        doc = _doc()
        doc.visual_marks = [
            # 500×500 / 1000×1000 = 25% 页面 → 异常大面积，必须进待确认
            VisualMark(kind=kind, page=0, rect=(10.0, 10.0, 510.0, 510.0),
                       confidence=0.99, note=f"{kind}=detect_only;/detect"),
            # 60×60 / 1000×1000 = 0.36% → 正常小码，保持自动涂黑
            VisualMark(kind=kind, page=0, rect=(600.0, 600.0, 660.0, 660.0),
                       confidence=0.99, note=f"{kind}=detect_only;/detect"),
        ]
        return doc, jobs._visual_entities(doc, [])

    def test_large_detect_only_code_becomes_pending_without_decision(self) -> None:
        for kind, etype in (("qrcode", "QRCODE"), ("barcode", "BARCODE")):
            with self.subTest(kind=kind):
                doc, ents = self._entities(kind)
                big = [e for e in ents
                       if e.spans and e.spans[0].rect
                       and (e.spans[0].rect[2] - e.spans[0].rect[0]) > 100]
                self.assertEqual(len(big), 1, f"{kind} 大面积候选未生成实体：{ents}")
                ent = big[0]
                self.assertEqual(ent.etype, etype)
                self.assertEqual(ent.status, "pending")
                self.assertIsNone(ent.decision, "待确认实体不得自动带 black 决策")
                self.assertIn("待确认", ent.label)
                self.assertEqual(ent.spans[0].page, 0)
                self.assertEqual(tuple(ent.spans[0].rect),
                                 (10.0, 10.0, 510.0, 510.0))
                self.assertIn(LARGE_CODE_WARNING, doc.warnings)

                # 小码不受该启发式影响，仍按 accepted + black 自动处理
                small = [e for e in ents
                         if e is not ent and e.spans and e.spans[0].rect]
                self.assertEqual(len(small), 1)
                self.assertEqual(small[0].status, "accepted")
                self.assertIsNotNone(small[0].decision)
                self.assertTrue(small[0].decision.black)

    def test_small_detect_only_code_stays_accepted(self) -> None:
        doc = _doc()
        doc.visual_marks = [
            VisualMark(kind="qrcode", page=0, rect=(10.0, 10.0, 60.0, 60.0),
                       confidence=0.99, note="qrcode=detect_only;/detect"),
        ]
        ents = jobs._visual_entities(doc, [])
        self.assertEqual(len(ents), 1)
        self.assertEqual(ents[0].status, "accepted")
        self.assertIsNotNone(ents[0].decision)
        self.assertNotIn(LARGE_CODE_WARNING, doc.warnings)


# ═════════════════════ 3. accept 后才进导出（black） ═════════════════════

class AcceptThenExportTest(unittest.TestCase):
    def setUp(self) -> None:
        doc = _doc()
        doc.visual_marks = [
            VisualMark(kind="qrcode", page=0, rect=(10.0, 10.0, 510.0, 510.0),
                       confidence=0.99, note="qrcode=detect_only;/detect"),
        ]
        self.entity = jobs._visual_entities(doc, [])[0]
        self.job = _job(doc, [self.entity])
        self.mgr = jobs.JobManager()
        self.mgr._jobs[self.job.id] = self.job
        self.captured: list[Entity] = []

        def fake_export(doc_, parser_, entities_, out_path, options):
            self.captured = list(entities_)
            Path(out_path).write_bytes(b"x")
            return ExportResult(path=str(out_path), warnings=[])

        self._patch = mock.patch.object(jobs.pipeline, "export", fake_export)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def _export(self, name: str) -> None:
        self.mgr._export_to(self.job, "ai", _TMP / name, do_verify=False)

    def test_pending_not_exported_until_accepted(self) -> None:
        self.assertEqual(self.entity.status, "pending")
        self.assertIsNone(self.entity.decision)

        self._export("before_accept.bin")
        self.assertEqual(self.captured, [],
                         "未确认的待确认实体不应进入导出")
        self.assertIsNone(self.entity.decision)

        res = self.mgr.update_entity(self.job.id, self.entity.id,
                                     {"action": "accept"})
        self.assertEqual(res["status"], "accepted")
        self.assertIsNotNone(self.entity.decision)
        self.assertEqual(self.entity.decision.strategy, "black")
        self.assertTrue(self.entity.decision.black)

        self._export("after_accept.bin")
        self.assertEqual([e.id for e in self.captured], [self.entity.id])
        self.assertEqual(self.captured[0].decision.strategy, "black")
        self.assertTrue(self.captured[0].decision.black)

    def test_accept_all_fills_black_for_rect_entity(self) -> None:
        # 文本类 pending（无 rect）不在本需求内：保持 decision=None 不造决策
        text_ent = Entity(
            id=Entity.new_id(), etype="OTHER", label="其他敏感", value="abc",
            spans=[Span(block_id="b1", start=0, end=3)],
            status="pending", decision=None,
        )
        self.job.entities.append(text_ent)

        out = self.mgr.accept_all(self.job.id)
        self.assertEqual(out["accepted"], 2)
        self.assertEqual(self.entity.status, "accepted")
        self.assertIsNotNone(self.entity.decision)
        self.assertTrue(self.entity.decision.black)
        self.assertIsNone(text_ent.decision)

        self._export("after_accept_all.bin")
        self.assertEqual([e.id for e in self.captured], [self.entity.id])

class PipelineExportFilterTest(unittest.TestCase):
    def test_pipeline_export_filter_excludes_no_decision(self) -> None:
        """直接验证 pipeline.export 的 active 过滤：无 decision 不进 parser。"""
        pending = Entity(
            id=Entity.new_id(), etype="QRCODE", label="二维码（待确认）", value="",
            spans=[Span(rect=(10.0, 10.0, 510.0, 510.0), page=0)],
            status="pending", decision=None,
        )
        accepted = Entity(
            id=Entity.new_id(), etype="QRCODE", label="二维码", value="",
            spans=[Span(rect=(550.0, 550.0, 610.0, 610.0), page=0)],
            status="accepted",
            decision=Decision(strategy="black", black=True, replacement=None),
        )
        parser = _CaptureParser()
        out = _TMP / "pipeline_filter.bin"
        pipeline.export(_doc(), parser, [pending, accepted], out, {"mode": "ai"})
        self.assertEqual([e.id for e in parser.seen], [accepted.id])


if __name__ == "__main__":
    unittest.main(verbosity=2)
