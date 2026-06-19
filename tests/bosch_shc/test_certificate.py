"""Tests for the integration's local certificate parsing helper.

The integration uses its own ``custom_components.bosch_shc.certificate`` (not
``boschshcpy.certificate``) so the Python 3.13+ crypto fix ships with the
integration itself. parse_certificate must:
- branch on ``hasattr(cert, "not_valid_before_utc")`` (no eager getattr default
  that crashes on cryptography that lacks the *_utc properties), and
- raise HomeAssistantError on a missing/invalid file.
"""

from datetime import datetime, timedelta, timezone

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.bosch_shc import certificate as cert_mod
from custom_components.bosch_shc.certificate import parse_certificate


def _build_selfsigned_pem(days_valid: int) -> bytes:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test")])
    now = datetime.now(timezone.utc)
    not_after = now + timedelta(days=days_valid)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_after - timedelta(days=400))
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM) + key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )


def test_parse_certificate_valid(tmp_path):
    cert_file = tmp_path / "cert.pem"
    cert_file.write_bytes(_build_selfsigned_pem(30))
    info = parse_certificate(str(cert_file))
    assert info.days_remaining >= 28
    assert info.not_after.tzinfo is not None


def test_parse_certificate_expired(tmp_path):
    cert_file = tmp_path / "cert.pem"
    cert_file.write_bytes(_build_selfsigned_pem(-2))
    info = parse_certificate(str(cert_file))
    assert info.days_remaining < 0


def test_parse_certificate_missing(tmp_path):
    with pytest.raises(HomeAssistantError, match="missing"):
        parse_certificate(str(tmp_path / "nope.pem"))


class _FakeOldCert:
    """cryptography < 42: naive not_valid_* properties, no *_utc variants."""

    def __init__(self, not_before: datetime, not_after: datetime) -> None:
        self.not_valid_before = not_before
        self.not_valid_after = not_after


def test_parse_certificate_old_cryptography_no_utc(tmp_path, monkeypatch):
    """REGRESSION: a cert without *_utc props must not raise AttributeError.

    The previous eager ``getattr(cert, "...", cert...._utc)`` default crashed
    here on every startup.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    fake = _FakeOldCert(now - timedelta(days=1), now + timedelta(days=10))
    assert not hasattr(fake, "not_valid_before_utc")
    monkeypatch.setattr(
        cert_mod.x509, "load_pem_x509_certificate", lambda *a, **k: fake
    )
    cert_file = tmp_path / "cert.pem"
    cert_file.write_bytes(b"dummy-pem")
    info = parse_certificate(str(cert_file))
    assert info.days_remaining >= 9
    assert info.not_after.tzinfo is not None
    assert info.not_before.tzinfo is not None
