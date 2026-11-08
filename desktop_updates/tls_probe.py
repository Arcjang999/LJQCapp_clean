"""Read-only TLS acceptance for the actual frozen update entry point.

This diagnostic uses the packaged production trust, transport and signature
verifier. It opens no UpdateManager, customer storage or Qt application. The
negative check's ephemeral TLS identity is unrelated to release signing.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
from pathlib import Path
import socket
import ssl
import sys
import tempfile
import threading

from desktop_updates.protocol import (TARGETS, UpdateError,
    validate_trust, verify_envelope)


def _bundled_ca(path: Path) -> bool:
    if not getattr(sys, "frozen", False):
        return False
    extracted = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)).resolve()
    roots = [extracted, Path(sys.executable).resolve().parent]
    # PyInstaller macOS relocates data to Resources and symlinks Frameworks.
    if extracted.name in ("Frameworks", "Resources") and extracted.parent.name == "Contents":
        roots.append(extracted.parent)
    return any(path.resolve().is_relative_to(root) for root in roots)


def _reject_unknown_self_signed(context: ssl.SSLContext) -> bool:
    """Perform a real loopback handshake and require an untrusted-chain error."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    now = datetime.now(timezone.utc)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "LJQC TLS diagnostic only")])
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(minutes=5))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"),
            x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
        .sign(key, hashes.SHA256()))
    with tempfile.TemporaryDirectory(prefix="ljqc-tls-probe-") as temporary:
        root = Path(temporary)
        key_file, certificate_file = root / "ephemeral-tls-key.pem", root / "ephemeral-tls-certificate.pem"
        with key_file.open("xb") as handle:
            key_file.chmod(0o600)
            handle.write(key.private_bytes(serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        certificate_file.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_context.minimum_version = ssl.TLSVersion.TLSv1_2
        server_context.load_cert_chain(certificate_file, key_file)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            listener.settimeout(8)
            errors = []
            def serve():
                try:
                    with listener.accept()[0] as connection:
                        connection.settimeout(5)
                        try:
                            with server_context.wrap_socket(connection, server_side=True):
                                pass
                        except ssl.SSLError:
                            pass  # A verified client must reject this private identity.
                except OSError as error:
                    errors.append(type(error).__name__)
            worker = threading.Thread(target=serve, name="LJQCTLSRejectionProbe", daemon=True)
            worker.start()
            rejected = False
            try:
                with socket.create_connection(listener.getsockname(), timeout=5) as connection:
                    try:
                        with context.wrap_socket(connection, server_hostname="localhost"):
                            raise UpdateError("TLS 验收未拒绝未知证书。")
                    except ssl.SSLCertVerificationError as error:
                        # OpenSSL's untrusted/self-signed chain codes; an unrelated
                        # connection/hostname failure must not count as this check.
                        rejected = error.verify_code in (18, 19, 20, 21)
            finally:
                worker.join(timeout=9)
            if worker.is_alive() or errors or not rejected:
                raise UpdateError("TLS 未知证书拒绝验收未完成。")
    return True


def run_tls_self_test(trust: dict) -> dict:
    import certifi
    from desktop_updates import transport
    # Reuse the webpage's lightweight signed-index verification. This imports
    # neither Streamlit nor database modules, and accepts no caller-provided key.
    from services.desktop_update_catalog_service import _catalog, _verify_index
    validate_trust(trust)
    ca_file = Path(certifi.where())
    context = transport._https_context()
    count = context.cert_store_stats().get("x509_ca", 0)
    if (not ca_file.is_file() or not count or context.verify_mode != ssl.CERT_REQUIRED
            or context.check_hostname is not True
            or context.minimum_version < ssl.TLSVersion.TLSv1_2):
        raise UpdateError("TLS 证书根或验证策略无效。")
    frozen = bool(getattr(sys, "frozen", False))
    bundled = _bundled_ca(ca_file)
    if frozen and not bundled:
        raise UpdateError("TLS 证书根未包含在更新组件中。")
    origin = trust["origin"].rstrip("/")
    url = origin + "/updates/desktop/dev/current/release-index.json"
    raw = transport.fetch_metadata_url(trust, url)
    index, issued = _verify_index(raw, trust, datetime.now(timezone.utc))
    _catalog(index, issued, origin)  # Bind file sets, URLs and manifest identities.
    immutable = (origin + "/updates/desktop/dev/releases/" + index["release_id"]
                 + "-" + str(index["sequence"]) + "/")
    manifests = {}
    for target in sorted(TARGETS):
        record = index["targets"][target]
        manifest_raw = transport.fetch_metadata_url(trust, immutable + target + "/latest.json")
        if (len(manifest_raw) != record["manifest_size_bytes"]
                or hashlib.sha256(manifest_raw).hexdigest() != record["manifest_sha256"]):
            raise UpdateError("TLS 验收的平台清单与发行索引不一致。")
        manifest = verify_envelope(manifest_raw, trust, target=target,
                                   installed_build=0, highest_sequence=0)
        for field in ("version", "build_number", "source_commit", "source_digest", "sequence"):
            if manifest.get(field) != index.get(field):
                raise UpdateError("TLS 验收的平台发行来源不一致。")
        manifests[target] = {"signature_verified": True, "sha256": record["manifest_sha256"]}
    rejected = _reject_unknown_self_signed(context)
    return {"format_version": 1, "product": "LJQCAppUpdater", "status": "passed",
        "frozen": frozen, "bundled_ca": bundled, "certifi_version": certifi.__version__,
        "ca_sha256": hashlib.sha256(ca_file.read_bytes()).hexdigest(), "trusted_ca_count": count,
        "hostname_verification": context.check_hostname, "certificate_verification": "CERT_REQUIRED",
        "minimum_tls_version": context.minimum_version.name,
        "unknown_self_signed_rejected": rejected, "production_origin": origin,
        "release_index_signature_verified": True, "release_index_sha256": hashlib.sha256(raw).hexdigest(),
        "release_id": index["release_id"], "published_sequence": index["sequence"],
        "platform_manifests": manifests, "business_storage_opened": False}
