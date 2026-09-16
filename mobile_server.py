"""Run the phone transcription backend without interrupting desktop capture.

python mobile_server.py --url https://YOUR_LAPTOP_IP:8091
Copy mobile-pairing.json to the phone over USB and select it in Pair laptop.
"""
import argparse
import os
import pathlib
from urllib.parse import urlparse

from fastapi import FastAPI
from certs import ensure_cert, local_ipv4s
from mobile_ingest import make_router, save_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    args = parser.parse_args()
    url = urlparse(args.url)
    if (url.scheme != "https" or url.hostname not in local_ipv4s()
            or url.path not in ("", "/") or url.query or url.fragment or url.username):
        parser.error("Use https:// followed by this laptop's LAN IPv4 address and port")
    from cryptography import x509
    import ipaddress
    import secrets
    import uvicorn

    root = pathlib.Path(os.environ.get("MEMORYOS_DATA", pathlib.Path(__file__).parent))
    root.mkdir(parents=True, exist_ok=True)
    cert, key = ensure_cert(root / "certs-mobile")
    parsed = x509.load_pem_x509_certificate(cert.read_bytes())
    addresses = parsed.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    if ipaddress.ip_address(url.hostname) not in addresses.get_values_for_type(x509.IPAddress):
        parser.error("Laptop address changed. Use the certificate's existing address or create a new certificate and re-pair.")
    token_file = root / "mobile-token.txt"
    if not token_file.exists():
        token_file.write_text(secrets.token_urlsafe(32), encoding="utf-8")
    token = token_file.read_text(encoding="utf-8").strip()
    if not token:
        parser.error("Mobile pairing token is empty")
    save_json(root / "mobile-pairing.json", {"url": args.url.rstrip("/"), "token": token,
                                             "certificate": cert.read_text(encoding="utf-8")})
    app = FastAPI(title="MemoryOS phone transcription")
    app.include_router(make_router(lambda: root, lambda: token))
    print("Phone backend ready. Copy mobile-pairing.json privately to your phone; do not share it.")
    uvicorn.run(app, host="0.0.0.0", port=url.port or 443,
                ssl_certfile=str(cert), ssl_keyfile=str(key), access_log=False)


if __name__ == "__main__":
    main()
