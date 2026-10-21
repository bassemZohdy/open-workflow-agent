"""Real HTTPS description/business peer for OpenAPI process-stop evidence."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import ssl
import subprocess
import threading
from contextlib import closing, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


class Receiver:
    def __init__(self, directory: Path, *, outcome: str, hold_response: bool):
        self.requests = []
        self.committed = threading.Event()
        self.release = threading.Event()
        self.changed = self.deleted = False
        self.database = directory / "receiver.sqlite3"
        with closing(sqlite3.connect(self.database)) as db:
            db.execute(
                "CREATE TABLE commits(operation_id TEXT PRIMARY KEY,parameters TEXT NOT NULL)"
            )
            db.commit()
        executable = shutil.which("openssl")
        if executable is None:
            candidate = (
                Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/usr/bin/openssl.exe"
            )
            executable = str(candidate) if candidate.is_file() else None
        assert executable is not None, "HTTPS fixture requires existing OpenSSL test tooling"
        self.certificate, self.key = directory / "receiver.pem", directory / "receiver.key"
        result = subprocess.run(
            [
                executable,
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-keyout",
                str(self.key),
                "-out",
                str(self.certificate),
                "-days",
                "1",
                "-subj",
                "/CN=localhost",
                "-addext",
                "subjectAltName=DNS:localhost,IP:127.0.0.1",
                "-addext",
                "basicConstraints=critical,CA:TRUE",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert result.returncode == 0, "ephemeral HTTPS certificate creation failed"
        peer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return None

            def reply(self, status, value):
                raw = json.dumps(value, allow_nan=False).encode()
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
                    pass

            def do_GET(self):
                certificate = self.connection.getpeercert()
                peer.requests.append(("GET", self.path, bool(certificate)))
                if self.path != "/spec.json" or certificate or "Authorization" in self.headers:
                    return self.reply(403, {"denied": True})
                if peer.deleted:
                    return self.reply(404, {"missing": True})
                self.reply(200, peer.document(changed=peer.changed))

            def do_POST(self):
                key = self.headers.get("Idempotency-Key", "")
                certificate = self.connection.getpeercert()
                parsed = urlsplit(self.path)
                peer.requests.append(("POST", key, bool(certificate)))
                if (
                    not certificate
                    or parsed.path != "/action"
                    or not key
                    or self.headers.get("X-OWA-Operation-ID") != key
                ):
                    return self.reply(403, {"denied": True})
                parameters = parse_qs(parsed.query, keep_blank_values=True)
                if set(parameters) != {"case", "value"} or any(
                    len(value) != 1 for value in parameters.values()
                ):
                    return self.reply(400, {"invalid": True})
                with closing(sqlite3.connect(peer.database, timeout=10)) as db:
                    try:
                        db.execute(
                            "INSERT INTO commits VALUES(?,?)",
                            (key, json.dumps(parameters, sort_keys=True)),
                        )
                        db.commit()
                    except sqlite3.IntegrityError:
                        return self.reply(409, {"duplicate": True})
                peer.committed.set()
                if hold_response:
                    peer.release.wait(timeout=120)
                self.reply(
                    503 if outcome == "fault" else 200,
                    {"rejected": True}
                    if outcome == "fault"
                    else None
                    if outcome == "null"
                    else {"committed": True, "original": parameters["value"][0]},
                )

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(self.certificate), str(self.key))
        context.load_verify_locations(cafile=str(self.certificate))
        context.verify_mode = ssl.CERT_OPTIONAL
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.origin = f"https://127.0.0.1:{self.server.server_port}"
        self.document_uri, self.endpoint = self.origin + "/spec.json", self.origin + "/action"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def document(self, *, changed=False):
        return {
            "openapi": "3.2.0",
            "info": {"title": "Original operation", "version": "2.0.0" if changed else "1.0.0"},
            "servers": [{"url": self.origin}],
            "components": {"securitySchemes": {"receiver": {"type": "mutualTLS"}}},
            "security": [{"receiver": []}],
            "paths": {
                "/changed" if changed else "/action": {
                    "post": {
                        "operationId": "commit",
                        "parameters": [
                            {
                                "name": name,
                                "in": "query",
                                "required": True,
                                "schema": {"type": "string"},
                            }
                            for name in ("case", "value")
                        ],
                        "responses": {
                            "200": {"description": "Committed"},
                            "503": {"description": "Known fault"},
                        },
                    }
                }
            },
        }

    def commits(self):
        with closing(sqlite3.connect(self.database)) as db:
            return db.execute("SELECT operation_id,parameters FROM commits").fetchall()

    def close(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        assert not self.thread.is_alive()


@contextmanager
def receiver(directory, **kwargs):
    peer = Receiver(directory, **kwargs)
    try:
        yield peer
    finally:
        peer.close()
