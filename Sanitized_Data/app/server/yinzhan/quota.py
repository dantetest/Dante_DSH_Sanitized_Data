# -*- coding: utf-8 -*-
"""授权门禁（W2：2026-11-05 起取消免费版，只有 3 天试用/付费授权）。

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
    """创建任务前的前置检查；返回规范化后的 pack_ids，不扣额度。

    2026-11-05 起取消免费版：未授权 / 试用结束一律拒绝，只有 3 天全功能试用
    和付费授权可以创建任务。
    """
    svc = service or SERVICE
    if not is_enforced():
        return pack_ids
    status = svc.status()
    if status.get("state") == "free":
        trial = status.get("trial") or {}
        if trial.get("used"):
            raise QuotaError(
                "license_required",
                "3 天全功能试用已结束，请购买个人版授权后继续。",
                403,
            )
        raise QuotaError(
            "license_required",
            "本版本不提供免费使用，请先在授权页开始 3 天全功能试用。",
            403,
        )
    return validate_packs(pack_ids)


def consume_job(job, service: LicenseService | None = None) -> None:
    """JobManager.create 成功后记录；未授权/试用结束直接拒绝。"""
    svc = service or SERVICE
    if not is_enforced():
        return
    status = svc.status()
    if status.get("state") == "free":
        trial = status.get("trial") or {}
        message = ("3 天全功能试用已结束，请购买个人版授权后继续。"
                   if trial.get("used")
                   else "本版本不提供免费使用，请先在授权页开始 3 天全功能试用。")
        raise QuotaError("license_required", message, 403)
    job.quota_free = False
    job.quota_charged = False
    return


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
    """无免费版：未授权/试用结束不暴露规则包；试用/付费全量。"""
    if not is_enforced():
        return index
    if is_free(service):
        return []
    return index
