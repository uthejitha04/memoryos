"""Self-signed certificate generation so phones can use HTTPS (mic capture
requires a secure context on non-localhost origins)."""

import datetime as dt
import ipaddress
import pathlib
import socket

CERT_DIR = pathlib.Path(__file__).parent / "certs"


def local_ipv4s() -> list[str]:
    ips = {"127.0.0.1"}
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except socket.gaierror:
        pass
    # UDP trick finds the outbound (Wi-Fi/LAN) interface even when the
    # hostname doesn't resolve to it
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return sorted(ips)


def ensure_cert(cert_dir: pathlib.Path = CERT_DIR) -> tuple[pathlib.Path, pathlib.Path]:
    """Return (cert_path, key_path), generating them if missing."""
    cert_dir.mkdir(exist_ok=True)
    cert_path, key_path = cert_dir / "server.crt", cert_dir / "server.key"
    if cert_path.exists() and key_path.exists():
        return cert_path, key_path

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "omi-transcriber")])
    sans = [x509.DNSName("localhost")]
    sans += [x509.IPAddress(ipaddress.ip_address(ip)) for ip in local_ipv4s()]
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName(sans), critical=False)
        .sign(key, hashes.SHA256())
    )
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption()))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path
