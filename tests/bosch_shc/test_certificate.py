"""Tests for certificate parsing helper."""
from datetime import datetime, timedelta, timezone

from homeassistant.exceptions import HomeAssistantError

from homeassistant.components.bosch_shc.certificate import parse_certificate


def _build_selfsigned_pem(days_valid: int) -> bytes:
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except Exception:  # pragma: no cover
        import pytest

        pytest.skip("cryptography not available")

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, u"Test")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=days_valid))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return (
        cert.public_bytes(serialization.Encoding.PEM)
        + key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )


def test_parse_certificate_valid(tmp_path):
    pem = _build_selfsigned_pem(5)
    cert_file = tmp_path / "cert.pem"
    cert_file.write_bytes(pem)
    info = parse_certificate(str(cert_file))
    assert info.days_remaining >= 4


def test_parse_certificate_expired(tmp_path):
    pem = _build_selfsigned_pem(-1)
    cert_file = tmp_path / "cert.pem"
    cert_file.write_bytes(pem)
    info = parse_certificate(str(cert_file))
    assert info.days_remaining < 0


def test_parse_certificate_missing(tmp_path):
    missing = tmp_path / "missing.pem"
    try:
        parse_certificate(str(missing))
    except HomeAssistantError as err:
        assert "missing" in str(err).lower()
    else:  # pragma: no cover
        assert False, "Expected HomeAssistantError"
