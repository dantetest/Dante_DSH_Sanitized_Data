# -*- coding: utf-8 -*-
"""离线授权（W1）：machine code / Ed25519 license / trial / 本地状态。"""
from __future__ import annotations

from .service import LicenseError, LicenseService, canonical_payload, sign_payload

__all__ = ["LicenseError", "LicenseService", "canonical_payload", "sign_payload"]
