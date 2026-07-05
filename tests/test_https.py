import ipaddress
import socket
import threading
import time

import httpx
import pytest
from cryptography import x509


def test_cert_generation_and_sans(tmp_path):
    from certs import ensure_cert, local_ipv4s

    cert_path, key_path = ensure_cert(tmp_path)
    assert cert_path.exists() and key_path.exists()
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    sans = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    dns = sans.get_values_for_type(x509.DNSName)
    ips = [str(i) for i in sans.get_values_for_type(x509.IPAddress)]
    assert "localhost" in dns
    for ip in local_ipv4s():
        assert ip in ips, f"LAN IP {ip} missing from cert SANs"

    # second call must reuse, not regenerate
    cert2, _ = ensure_cert(tmp_path)
    assert cert2.read_bytes() == cert_path.read_bytes()


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_tls_server_serves_status(stub_hub, tmp_path):
    import uvicorn

    import server
    from certs import ensure_cert

    cert_path, key_path = ensure_cert(tmp_path)
    port = _free_port()
    config = uvicorn.Config(server.app, host="127.0.0.1", port=port,
                            log_level="error", ssl_certfile=str(cert_path),
                            ssl_keyfile=str(key_path))
    srv = uvicorn.Server(config)
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    deadline = time.time() + 15
    while not srv.started and time.time() < deadline:
        time.sleep(0.1)
    assert srv.started, "TLS server failed to start"
    try:
        r = httpx.get(f"https://127.0.0.1:{port}/status", verify=False, timeout=10)
        assert r.status_code == 200
        assert r.json()["type"] == "status"
        # plain http against the TLS port must fail
        with pytest.raises(httpx.HTTPError):
            httpx.get(f"http://127.0.0.1:{port}/status", timeout=5)
    finally:
        srv.should_exit = True
        t.join(timeout=10)
