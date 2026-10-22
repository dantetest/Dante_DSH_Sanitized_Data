# -*- coding: utf-8 -*-
"""本地文件日志自测（``yinzhan.logging_setup``）。

覆盖交付要求：
  * 日志落盘、格式可 grep、启动/错误/心跳/退出行；
  * 每天 0 点按日期翻转（server.log → server-YYYY-MM-DD.log）；
  * 保留天数清理旧归档；
  * 进程跨天停机后重启，旧 server.log 自动归档；
  * 请求日志不落 query string / 请求体 / 文件名；
  * 客户端 IP 默认不记，显式开启才记；
  * 日志目录不可写时降级，不拖死服务。

只写系统临时目录；可用 ``SD_LOG_TEST_ROOT`` 指定可写根目录
（Windows 受限令牌下系统临时目录不可用时用）。
"""
from __future__ import annotations

import atexit
import logging
import logging.handlers
import os
import re
import shutil
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

_TESTS = Path(__file__).resolve().parent
_SERVER = _TESTS.parent / "server"
if str(_SERVER) not in sys.path:
    sys.path.insert(0, str(_SERVER))


def _pick_root() -> tuple[Path, bool]:
    env = os.environ.get("SD_LOG_TEST_ROOT")
    if env:
        root = Path(env)
        root.mkdir(parents=True, exist_ok=True)
        return root, False
    # 不用 tempfile.mkdtemp：Windows 受限令牌下 0o700 目录会拒绝后续子目录创建
    root = Path(tempfile.gettempdir()) / f"sd_logtest_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    root.mkdir(parents=True, exist_ok=True)
    return root, True


_ROOT, _OWN_ROOT = _pick_root()
if _OWN_ROOT:
    atexit.register(lambda: shutil.rmtree(_ROOT, ignore_errors=True))
_DATA = _ROOT / "data"
_OUT = _ROOT / "out"
# 测试强制用独立数据目录：即使外部已设 YINZHAN_DATA_DIR，也不写真实 data/
os.environ["YINZHAN_DATA_DIR"] = str(_DATA)
os.environ["YINZHAN_OUTPUT_DIR"] = str(_OUT)
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

from yinzhan import logging_setup  # noqa: E402

_ARCHIVE_RE = re.compile(r"^server-\d{4}-\d{2}-\d{2}(?:-\d+)?\.log$")


class LoggingSetupTest(unittest.TestCase):
    def setUp(self) -> None:
        logging_setup.shutdown_logging()
        self.case = _ROOT / f"case_{self._testMethodName}_{uuid.uuid4().hex[:6]}"
        self.case.mkdir(parents=True, exist_ok=True)
        self.logs = self.case / "logs"

    def tearDown(self) -> None:
        logging_setup.shutdown_logging()
        shutil.rmtree(self.case, ignore_errors=True)

    def _file_handler(self) -> logging.Handler:
        target = str(self.logs / "server.log")
        for handler in logging.getLogger("yinzhan").handlers:
            if getattr(handler, "baseFilename", "") == target:
                return handler
        self.fail("找不到 server.log 的 TimedRotatingFileHandler")

    # ── 基础落盘/格式 ──
    def test_writes_startup_request_error_heartbeat(self):
        state = logging_setup.setup_logging(
            log_dir=self.logs, console=False, keep_days=5, heartbeat_sec=0)
        self.assertIsNotNone(state.log_path)
        self.assertTrue(state.log_path.parent.is_dir())
        logging_setup.log_startup({"entry": "unit"})
        logging_setup.log_request("GET", "/api/health", 200, 12, 1.5)
        logging_setup.log_error("单元测试错误")
        logging_setup.log_heartbeat({"active_jobs": 2})
        logging_setup.log_shutdown("单测结束")
        logging_setup.flush()

        text = (self.logs / "server.log").read_text("utf-8")
        self.assertIn("[INFO] [yinzhan.server] start ", text)
        self.assertIn("entry=unit", text)
        self.assertIn("req method=GET path=/api/health status=200", text)
        self.assertIn("[ERROR] [yinzhan.api] 单元测试错误", text)
        self.assertIn("heartbeat ", text)
        self.assertIn("active_jobs=2", text)
        self.assertIn("reason=单测结束", text)

    def test_request_line_drops_query_and_control_chars(self):
        line = logging_setup.format_request(
            "GET", "/api/jobs/j_1/file?mode=ai&path=/secret/x.pdf#frag",
            200, 42, 3.456, client_ip="")
        self.assertIn("path=/api/jobs/j_1/file", line)
        self.assertNotIn("mode=ai", line)
        self.assertNotIn("secret", line)
        self.assertNotIn("?", line)
        self.assertIn("ms=3.5", line)
        # 控制字符/空格不能把一行日志拆断（防止伪造日志行）
        dirty = logging_setup.format_request(
            "POST", "/api/x\n[ERROR] fake", 500, 0, 0.0)
        self.assertEqual(dirty.count("\n"), 0)
        self.assertNotIn("[ERROR] fake", dirty)

    def test_error_and_warning_sanitized(self):
        """Header/异常进日志前要清 query、换行和数据目录里的上传原文件名。"""
        logging_setup.setup_logging(
            log_dir=self.logs, console=False, keep_days=5, heartbeat_sec=0)
        leaked_name = "病历_张三.pdf"
        upload_path = _DATA / "uploads" / ("u_ab12cd34ef56_" + leaked_name)
        logging_setup.log_error(
            f"解析失败 path={upload_path}?token=secret\n第二行伪造")
        logging_setup.log_warning("origin=a.example\r\n[ERROR] forged?x=1")
        logging_setup.flush()

        text = (self.logs / "server.log").read_text("utf-8")
        self.assertNotIn(leaked_name, text)
        self.assertNotIn("secret", text)
        self.assertNotIn("?x=1", text)
        # 警告行不能被 CRLF 拆成第二行伪造记录
        self.assertNotIn("\n[ERROR] forged", text)
        self.assertIn("[WARNING] [yinzhan.api] origin=a.example", text)
        # 错误堆栈允许续行，但数据目录被替换成占位符
        self.assertIn("<data>", text)
        self.assertIn("<upload>", text)

    # ── 日期翻转 ──
    def test_rollover_creates_dated_archive(self):
        logging_setup.setup_logging(
            log_dir=self.logs, console=False, keep_days=5, heartbeat_sec=0)
        log = logging_setup.get_logger("unit.rollover")
        log.info("翻转前的内容")
        handler = self._file_handler()
        handler.rolloverAt = int(time.time()) - 1      # 模拟跨天
        log.info("翻转后的内容")
        logging_setup.flush()

        current = (self.logs / "server.log").read_text("utf-8")
        archives = [p for p in self.logs.iterdir()
                    if p.is_file() and _ARCHIVE_RE.match(p.name)]
        self.assertEqual(len(archives), 1, [p.name for p in archives])
        archived = archives[0].read_text("utf-8")
        self.assertIn("翻转前的内容", archived)
        self.assertIn("翻转后的内容", current)
        self.assertNotIn("翻转前的内容", current)

    def test_stale_current_archived_at_startup(self):
        self.logs.mkdir(parents=True, exist_ok=True)
        current = self.logs / "server.log"
        current.write_text("[2026-10-20 10:00:00] [ERROR] [yinzhan.api] 旧错误\n",
                           "utf-8")
        old_ts = time.time() - 2 * 86400
        os.utime(current, (old_ts, old_ts))
        archived = logging_setup._archive_stale_current(self.logs)
        self.assertIsNotNone(archived)
        self.assertFalse(current.exists())
        self.assertTrue(_ARCHIVE_RE.match(archived.name), archived.name)
        self.assertIn("旧错误", archived.read_text("utf-8"))

    def test_retention_keeps_newest_archives(self):
        logging_setup.setup_logging(
            log_dir=self.logs, console=False, keep_days=2, heartbeat_sec=0)
        for day in ("2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"):
            (self.logs / f"server-{day}.log").write_text("x", "utf-8")
        handler = self._file_handler()
        removed = handler.cleanup()
        names = sorted(p.name for p in self.logs.glob("server-*.log"))
        self.assertEqual(names, ["server-2026-01-03.log", "server-2026-01-04.log"])
        self.assertEqual(removed, 2)

    # ── 心跳 / IP / 降级 ──
    def test_heartbeat_thread_writes_repeatedly(self):
        logging_setup.setup_logging(
            log_dir=self.logs, console=False, keep_days=5, heartbeat_sec=1)
        hb = logging_setup.start_heartbeat(
            provider=lambda: {"active_jobs": 3}, interval=1)
        self.assertIsNotNone(hb)
        path = self.logs / "server.log"
        deadline = time.time() + 5
        text = ""
        while time.time() < deadline:
            text = path.read_text("utf-8") if path.exists() else ""
            if text.count("heartbeat ") >= 2:
                break
            time.sleep(0.2)
        logging_setup.stop_heartbeat()
        self.assertGreaterEqual(text.count("heartbeat "), 2, text)
        self.assertIn("active_jobs=3", text)

    def test_client_ip_default_off_and_opt_in(self):
        logging_setup.setup_logging(log_dir=self.logs, console=False,
                                    heartbeat_sec=0, client_ip=False)
        self.assertEqual(logging_setup.pick_client_ip(
            "127.0.0.1", "203.0.113.9", "198.51.100.7"), "")
        logging_setup.setup_logging(log_dir=self.logs, force=True, console=False,
                                    heartbeat_sec=0, client_ip=True)
        self.assertEqual(logging_setup.pick_client_ip(
            "127.0.0.1", "203.0.113.9", ""), "203.0.113.9")
        self.assertEqual(logging_setup.pick_client_ip(
            "127.0.0.1", "", "198.51.100.7, 10.0.0.1"), "198.51.100.7")
        self.assertEqual(logging_setup.pick_client_ip(
            "192.0.2.5", "203.0.113.9", ""), "192.0.2.5")

    def test_unwritable_log_dir_falls_back(self):
        blocked = self.case / "blocked"
        blocked.write_text("我是文件，不是目录", "utf-8")   # mkdir 必失败
        state = logging_setup.setup_logging(
            log_dir=blocked, console=False, heartbeat_sec=0)
        self.assertTrue(state.file_error, "应记录降级原因")
        self.assertIsNotNone(state.log_path, "应回退到数据目录下的 logs")
        self.assertTrue(state.log_path.parent.is_dir())
        logging_setup.log_error("降级后仍可写")
        logging_setup.flush()
        self.assertIn("降级后仍可写", state.log_path.read_text("utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
