# -*- coding: utf-8 -*-
"""免费版额度与前置拦截（W2）。

所有判断收在本模块 + licensing，不污染解析/检测管线。
- 源码开发模式默认不启用（现有测试/开发体验）；PyInstaller 打包后默认启用。
- 可用 ``YINZHAN_ENFORCE_FREE_TIER=1`` 在源码模式启用做验收。
"""
from __future__ import annotations

import os
from pathlib import Path

from . import paths
from .config import PAYWALL_ENABLED
from .licensing import tiers
from .licensing.service import LicenseError, LicenseService

#: 免费版可用规则包；付费包一律拒绝，禁止静默过滤
FREE_PACKS = ("common",)
FREE_FORMATS = {"pdf", "docx", "xlsx", "image", "text"}
DENIED_FORMATS = {"tiff", "mesh"}
DENIED_SUFFIXES = {".tif", ".tiff", ".obj", ".ply", ".glb", ".stl"}

SERVICE = LicenseService()


class QuotaError(LicenseError):
    pass


def is_enforced() -> bool:
    if not PAYWALL_ENABLED:
        return False
    if os.environ.get("YINZHAN_ENFORCE_FREE_TIER") == "1":
        return True
    return bool(paths.FROZEN)


def _status(service: LicenseService | None = None) -> dict:
    return (service or SERVICE).status()


def is_free(service: LicenseService | None = None) -> bool:
    return _status(service).get("state") == "free"


def _known_packs() -> set[str]:
    try:
        from . import rules
        return set(rules.load_packs())
    except Exception:
        return set()


def validate_packs(pack_ids: list[str] | None) -> list[str] | None:
    """校验 pack id 是否真实存在；未知/非法 → 拒绝，不交给 compile_rules 静默丢空。"""
    if not pack_ids:
        return None
    known = _known_packs()
    bad = [p for p in pack_ids if p not in known]
    if bad:
        raise QuotaError("pack_requires_paid",
                         "该行业规则包不可用，请切换通用规则，或升级后使用。", 400)
    return list(dict.fromkeys(pack_ids))


def _precheck_pages(path: Path, fmt: str) -> int | None:
    if fmt == "pdf":
        try:
            import fitz  # PyMuPDF
            with fitz.open(str(path)) as doc:
                return int(doc.page_count)
        except Exception:
            return None
    if fmt in ("image", "text"):
        return 1
    return None


def precheck(path: Path, fmt: str, pack_ids: list[str] | None = None,
             service: LicenseService | None = None) -> list[str] | None:
    """创建任务前的前置检查；返回规范化后的 pack_ids，不扣额度。"""
    svc = service or SERVICE
    if not is_enforced():
        return pack_ids
    status = svc.status()
    if status.get("state") != "free":
        return validate_packs(pack_ids)
    # 1) 格式
    suffix = path.suffix.lower()
    if fmt in DENIED_FORMATS or suffix in DENIED_SUFFIXES:
        raise QuotaError(
            "free_format",
            "TIF / 三维文件需要个人版及以上。免费版可先用 PDF、Word、Excel、图片。",
            403,
        )
    if fmt not in FREE_FORMATS:
        raise QuotaError("free_format", "免费版暂不支持这种格式，请升级后使用。", 403)
    # 2) 文件大小
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    if size > int(tiers.FREE_LIMITS.get("max_file_mb") or 100) * 1024 * 1024:
        raise QuotaError("free_size_limit", "免费版单文件限 100MB。更大的文件建议升级后处理。", 403)
    # 3) 规则包：免费只允许通用包；None 默认 common
    normalized = ["common"]
    if pack_ids:
        bad = [p for p in pack_ids if p not in FREE_PACKS]
        if bad:
            raise QuotaError(
                "pack_requires_paid",
                "该行业规则包需要个人版及以上；请切换通用规则，或升级后使用。",
                403,
            )
        normalized = ["common"]
    # 4) 页数预判（PDF/图片）；Office 解析后再查
    pages = _precheck_pages(path, fmt)
    if pages is not None and pages > int(tiers.FREE_LIMITS.get("max_pages") or 20):
        raise QuotaError(
            "free_page_limit",
            f"免费版单份最多 20 页，这份文件有 {pages} 页。升级后不限页数。",
            403,
        )
    # 5) 当日剩余额度
    quota = svc.quota_status()
    if int(quota.get("remaining") or 0) <= 0:
        raise QuotaError(
            "quota_exceeded",
            "今天的 3 份免费额度用完了。明天 0 点恢复；也可以现在升级，今天就能继续。",
            403,
        )
    return normalized


def consume_job(job, service: LicenseService | None = None) -> None:
    """JobManager.create 成功后扣 1 份；非免费/未启用时不计数。"""
    svc = service or SERVICE
    if not is_enforced():
        return
    status = svc.status()
    if status.get("state") != "free":
        job.quota_free = False
        job.quota_charged = False
        return
    svc.consume_free_job()
    job.quota_free = True
    job.quota_charged = True
    job.quota_refunded = False


def refund_job(job, service: LicenseService | None = None) -> None:
    if getattr(job, "quota_charged", False) and not getattr(job, "quota_refunded", False):
        (service or SERVICE).refund_free_job()
        job.quota_refunded = True


def after_parse(job, pages: int, ocr_pages: int | None = None,
                service: LicenseService | None = None) -> None:
    """解析后页数/OCR 上限；超限退额度并抛错，绝不进入检测。"""
    if not getattr(job, "quota_charged", False):
        return
    if int(pages or 0) > int(tiers.FREE_LIMITS.get("max_pages") or 20):
        refund_job(job, service)
        raise QuotaError(
            "free_page_limit",
            f"免费版单份最多 20 页，这份文件有 {pages} 页。升级后不限页数。",
            403,
        )
    if ocr_pages is not None and int(ocr_pages) > 0:
        # H-16：当日 OCR 页数用量落库（此前只暴露字段不累加，/api/quota/status 恒为 0）
        try:
            (service or SERVICE).add_ocr_pages(int(ocr_pages))
        except Exception:
            pass
    if ocr_pages is not None and int(ocr_pages) > int(tiers.FREE_LIMITS.get("ocr_pages_per_file") or 10):
        refund_job(job, service)
        raise QuotaError(
            "free_ocr_limit",
            "免费版单份最多识别 10 页扫描件。升级后能整份识别。",
            403,
        )


def visible_packs(index: list[dict], service: LicenseService | None = None) -> list[dict]:
    """免费版 /api/packs 只暴露通用包；付费/试用全量。"""
    if not is_enforced():
        return index
    if not is_free(service):
        return index
    return [p for p in index if p.get("id") in FREE_PACKS]
