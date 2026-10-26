# -*- coding: utf-8 -*-
"""凭据类规则回归（2026-10-25 QA E-01）。

真实测试发现：``cn.private_key`` 只匹配 ``-----BEGIN ... PRIVATE KEY-----`` 这一行，
base64 正文与 END 行原样留在 human/ai 成品里；``password=``/``密码：``/``api_key=``
等键值形态也不命中。本测试锁定：

1. PEM 私钥整块（BEGIN + 正文 + END）必须被识别为 SECRET，导出后原文全消失；
2. 常见口令/密钥/令牌键值对必须命中，且只涂值、不误涂标签；
3. 正常文本（无凭据语义）不被误报。

全部为虚构测试数据，不写入仓库数据目录。
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parents[1]
SERVER = REPO / "app" / "server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

TMP = Path(tempfile.mkdtemp(prefix="yz_secret_"))
os.environ.setdefault("YINZHAN_DATA_DIR", str(TMP / "data"))
os.environ.setdefault("YINZHAN_OUTPUT_DIR", str(TMP / "out"))

from yinzhan import pipeline, registry     # noqa: E402
from yinzhan.jobs import MANAGER, _load_rules  # noqa: E402
from yinzhan.udm import ParseContext       # noqa: E402

PEM_BODY_1 = ("b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW"
              "QyNTUxOQAAACDummyDummyDummyDummyDummyDummyDummyDummyDummyDummyD")
PEM_BODY_2 = ("FakeFakeFakeFakeFakeFakeFakeFakeFakeFakeFakeFakeFakeFakeFakeFake"
              "FakeFakeFakeFakeFakeFakeFakeFakeFakeFakeFakeFakeFakeFakeFakeFake")

SECRETS = [
    "Abc123!@#",
    "S3cr3t#2026",
    "django-insecure-abc123def456ghi789",
    "sk-proj-AbCdEf0123456789AbCdEf",
    "ghp_AbCdEf0123456789AbCdEf",
]

TEXT_LINES = [
    "# 部署配置（合成测试数据，非真实凭据）",
    f"password={SECRETS[0]}",
    f"数据库密码：{SECRETS[1]}",
    f"SECRET_KEY={SECRETS[2]}",
    f"api_key={SECRETS[3]}",
    f"token: {SECRETS[4]}",
    "token 桶容量 100（这句没有凭据，不应被误涂）",
    "-----BEGIN OPENSSH PRIVATE KEY-----",
    PEM_BODY_1,
    PEM_BODY_2,
    "-----END OPENSSH PRIVATE KEY-----",
    "注：以上均为虚构测试数据",
]


def _write_txt(path: Path) -> None:
    path.write_text("\n".join(TEXT_LINES) + "\n", encoding="utf-8")


def _detect_text(path: Path):
    from yinzhan.parsers.text_parser import TextParser
    doc = TextParser().parse(path, ParseContext())
    rules, _pack, _index = _load_rules(doc.fmt)
    det = pipeline.detect(doc, rules, "standard")
    return doc, det


class SecretRuleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        registry.load_plugins()
        cls.txt = TMP / "secret_plaintext.txt"
        _write_txt(cls.txt)

    def test_pem_private_key_whole_block(self):
        _doc, det = _detect_text(self.txt)
        pem = [e for e in det.entities
               if e.etype == "SECRET" and "PRIVATE KEY" in e.value]
        self.assertTrue(pem, "私钥块必须识别，实体="
                        + str([e.value[:30] for e in det.entities if e.etype == "SECRET"]))
        value = max((e.value for e in pem), key=len)
        self.assertIn("BEGIN OPENSSH PRIVATE KEY", value)
        self.assertIn("END OPENSSH PRIVATE KEY", value, "私钥块需覆盖到 END 行")
        self.assertIn(PEM_BODY_1[:32], value.replace("\n", ""), "私钥正文需整块覆盖")

    def test_key_value_secrets(self):
        _doc, det = _detect_text(self.txt)
        values = [e.value for e in det.entities if e.etype == "SECRET"]
        for secret in SECRETS:
            self.assertTrue(any(secret in value for value in values),
                            f"凭据未命中：{secret}；实体={values}")
        # 只涂值不涂标签：实体值里不应包含 password=/密码： 等标签
        for value in values:
            self.assertNotIn("password=", value.lower())
            self.assertNotIn("密码：", value)

    def test_benign_text_not_flagged(self):
        _doc, det = _detect_text(self.txt)
        values = [e.value for e in det.entities if e.etype == "SECRET"]
        self.assertFalse(any("桶容量" in value for value in values),
                         f"无凭据语义的文本被误报：{values}")

    def test_export_removes_all_secrets(self):
        job = MANAGER.create(self.txt, strictness="standard", pack_ids=None,
                             id_card_mode="fields", time_blur=False, origin="api")
        while job.stage not in ("ready", "failed"):
            time.sleep(0.2)
        self.assertEqual(job.stage, "ready", job.error)
        for mode in ("ai", "human"):
            out = TMP / f"secret_out_{mode}.txt"
            out.unlink(missing_ok=True)
            result = MANAGER._export_to(job, mode, out, do_verify=True)
            self.assertTrue(out.is_file(), f"{mode} 成品不存在")
            body = out.read_text(encoding="utf-8", errors="replace")
            for secret in SECRETS:
                self.assertNotIn(secret, body, f"{mode} 成品残留凭据：{secret}")
            self.assertNotIn("BEGIN OPENSSH PRIVATE KEY", body,
                             f"{mode} 成品残留私钥头")
            self.assertNotIn(PEM_BODY_1[:32], body.replace("\n", ""),
                             f"{mode} 成品残留私钥正文")
            self.assertEqual(result["verify"].get("residual"), 0,
                             f"{mode} 成品复检 residual 应为 0")


if __name__ == "__main__":
    unittest.main(verbosity=2)
