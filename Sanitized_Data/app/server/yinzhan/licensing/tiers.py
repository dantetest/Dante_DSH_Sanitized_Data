# -*- coding: utf-8 -*-
"""套餐/功能位/限额（单一事实来源，对应 docs/impl/licensing.md §3.3）。"""
from __future__ import annotations

#: 所有已知功能位；license payload 里的 features 必须是这个集合的子集。
KNOWN_FEATURES = {
    "all_packs", "custom_packs", "tif", "d3d", "mcp", "batch",
    "audit_export", "team_admin", "org_deploy", "ocr",
}

TRIAL_FEATURES = ["all_packs", "tif", "d3d", "mcp", "ocr"]
PERSONAL_FEATURES = ["all_packs", "tif", "d3d", "ocr"]
PRO_FEATURES = ["all_packs", "custom_packs", "tif", "d3d", "mcp", "batch",
                "audit_export", "ocr"]
TEAM_FEATURES = PRO_FEATURES + ["team_admin"]
ORG_FEATURES = TEAM_FEATURES + ["org_deploy"]

FREE_LIMITS = {
    "jobs_per_day": 3,
    "max_pages": 20,
    "ocr_pages_per_file": 10,
    "max_file_mb": 100,
    "packs": "common",
    "formats": ["pdf", "docx", "xlsx", "image", "text"],
}

TIERS = {
    "free": {"edition": "免费版", "features": [], "limits": FREE_LIMITS},
    "trial": {"edition": "试用版", "features": TRIAL_FEATURES, "limits": None},
    "personal": {"edition": "个人版", "features": PERSONAL_FEATURES, "limits": None},
    "pro": {"edition": "专业版", "features": PRO_FEATURES, "limits": None},
    "team": {"edition": "团队版", "features": TEAM_FEATURES, "limits": None},
    "org": {"edition": "机构版", "features": ORG_FEATURES, "limits": None},
}


def tier_info(tier: str) -> dict:
    return TIERS.get(tier or "free", TIERS["free"])


def features_for(tier: str) -> list[str]:
    return list(tier_info(tier)["features"])


def limits_for(tier: str):
    return tier_info(tier)["limits"]


def valid_sku(sku: str) -> bool:
    return sku in ("personal", "pro", "team", "org")
