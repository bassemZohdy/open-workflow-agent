"""Real local TLS issuer/resource witness; no external issuer or paid API."""

from __future__ import annotations

import json
import os
import shutil
import ssl
import subprocess
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs


class AuthTlsReceiver:
    def __init__(self, directory: Path):
        openssl = shutil.which("openssl")
        if openssl is None:
            candidate = (
                Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/usr/bin/openssl.exe"
            )
            openssl = str(candidate) if candidate.is_file() else None
        assert openssl is not None, "TLS acceptance requires existing OpenSSL test tooling"
        self.certificates = {}
        self.requests = []
        self.token_response = {
            "access_token": "real-issued-token",
            "token_type": "Bearer",
            "expires_in": 60,
        }
        for name in ("localhost", "client-A", "client-B"):
            certificate, key = directory / (name + ".pem"), directory / (name + ".key")
            completed = subprocess.run(
                [
                    openssl,
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-nodes",
                    "-keyout",
                    str(key),
                    "-out",
                    str(certificate),
                    "-days",
                    "1",
                    "-subj",
                    "/CN=" + name,
                    "-addext",
                    "subjectAltName=DNS:localhost,IP:127.0.0.1",
                    "-addext",
                    "basicConstraints=critical,CA:TRUE",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                check=False,
            )
            assert completed.returncode == 0, "ephemeral certificate generation failed"
            self.certificates[name] = (certificate, key)
        clients = directory / "trusted-clients.pem"
        clients.write_bytes(
            b"\n".join(self.certificates[name][0].read_bytes() for name in ("client-A", "client-B"))
        )
        self.trusted_clients_bundle = clients
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return None

            def do_POST(self):
                body = self.rfile.read(min(int(self.headers.get("Content-Length", "0")), 65_536))
                certificate = self.connection.getpeercert()
                subject = (
                    dict(item for group in certificate.get("subject", ()) for item in group)
                    if certificate
                    else {}
                )
                identity = subject.get("commonName")
                receiver.requests.append((self.path, dict(self.headers), body, identity))
                status = 200
                if self.path == "/token":
                    form = parse_qs(body.decode())
                    if (
                        form.get("client_id") != ["local-client"]
                        or form.get("client_secret") != ["private-local-secret"]
                        or form.get("resource") != [receiver.origin + "/resource"]
                    ):
                        status = 401
                    value = receiver.token_response
                elif self.path == "/mtls":
                    if identity not in {"client-A", "client-B"}:
                        status = 403
                    value = {"identity": identity}
                else:
                    if self.headers.get("Authorization") != "Bearer real-issued-token":
                        status = 403
                    value = {"accepted": True}
                encoded = json.dumps(value).encode()
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(encoded)))
                    self.end_headers()
                    self.wfile.write(encoded)
                except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(*self.certificates["localhost"])
        context.load_verify_locations(cafile=str(clients))
        context.verify_mode = ssl.CERT_OPTIONAL
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.origin = f"https://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def ca_bundle(self) -> str:
        return str(self.certificates["localhost"][0])

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        assert not self.thread.is_alive(), "TLS fixture did not finish cleanup"


@contextmanager
def auth_tls_receiver(directory):
    receiver = AuthTlsReceiver(directory)
    try:
        yield receiver
    finally:
        receiver.close()
