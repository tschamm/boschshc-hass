"""Helper functions for Bosch SHC client certificate handling."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from homeassistant.exceptions import HomeAssistantError

try:
    from cryptography import x509  # type: ignore
    from cryptography.hazmat.backends import default_backend  # type: ignore
except Exception as exc:  # pragma: no cover - cryptography should exist in HA
    raise HomeAssistantError("cryptography library not available") from exc


class CertificateInfo(NamedTuple):
    """Parsed certificate info."""

    not_before: datetime
    not_after: datetime
    days_remaining: int


def parse_certificate(cert_path: str) -> CertificateInfo:
    """Parse a PEM certificate and return validity information.

    Raises HomeAssistantError if file missing or invalid.
    """
    path = Path(cert_path)
    if not path.is_file():
        raise HomeAssistantError(f"Certificate file missing: {cert_path}")

    data = path.read_bytes()
    try:
        cert = x509.load_pem_x509_certificate(data, default_backend())
    except Exception as exc:  # pragma: no cover - defensive
        raise HomeAssistantError(f"Invalid certificate: {cert_path}") from exc

    now = datetime.now(timezone.utc)
    # Use *_utc properties when available (cryptography >= 41), fallback otherwise.
    not_before = getattr(cert, "not_valid_before_utc", cert.not_valid_before)
    if not_before.tzinfo is None:
        not_before = not_before.replace(tzinfo=timezone.utc)
    not_after = getattr(cert, "not_valid_after_utc", cert.not_valid_after)
    if not_after.tzinfo is None:
        not_after = not_after.replace(tzinfo=timezone.utc)
    days_remaining = int((not_after - now).total_seconds() // 86400)
    return CertificateInfo(not_before, not_after, days_remaining)
