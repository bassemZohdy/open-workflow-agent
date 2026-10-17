"""Real bounded HTTPS test receiver with durable dedupe and outcome lookup."""

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
from typing import Any
from urllib.parse import parse_qs, urlsplit

from open_workflow_agent.external_recovery import HTTP_RECEIVER_PROFILE, receiver_request_digest

RECEIVER_ID = "durable-test-receiver"
PRINCIPAL = "receiver-principal"
TOKEN_ENV = "OWA_TEST_RECEIVER_TOKEN"
TOKEN = "only-a-deterministic-test-credential"


class Receiver:
    def __init__(self, directory: Path, *, outcome: str = "value", hold_response: bool = False):
        self.directory = directory
        self.outcome = outcome
        self.hold_response = hold_response
        self.committed = threading.Event()
        self.release = threading.Event()
        self.requests: list[tuple[str, str]] = []
        self.replacement_proof: Any = None
        self.lookup_status = 200
        self.lookup_barrier: threading.Event | None = None
        self.lookup_started = threading.Event()
        self.database = directory / "receiver.sqlite3"
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "CREATE TABLE commits(operation_id TEXT PRIMARY KEY,request_digest TEXT NOT NULL,"
                "proof TEXT NOT NULL,status BIGINT NOT NULL,response TEXT NOT NULL)"
            )
            connection.commit()
        openssl = shutil.which("openssl")
        if openssl is None:
            candidate = (
                Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/usr/bin/openssl.exe"
            )
            openssl = str(candidate) if candidate.is_file() else None
        if openssl is None:
            raise AssertionError("HTTPS acceptance requires the existing OpenSSL test tool")
        self.certificate = directory / "receiver-ca.pem"
        self.private_key = directory / "receiver-key.pem"
        result = subprocess.run(
            [
                openssl,
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-keyout",
                str(self.private_key),
                "-out",
                str(self.certificate),
                "-days",
                "1",
                "-subj",
                "/CN=localhost",
                "-addext",
                "subjectAltName=DNS:localhost,IP:127.0.0.1",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
        assert result.returncode == 0, "ephemeral HTTPS certificate generation failed"
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return None

            def _reply(self, status: int, value: Any) -> None:
                encoded = json.dumps(value, allow_nan=False).encode()
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(encoded)))
                    self.end_headers()
                    self.wfile.write(encoded)
                except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
                    pass

            def _authorized(self) -> bool:
                if self.headers.get("Authorization") != "Bearer " + TOKEN:
                    self._reply(401, {"denied": True})
                    return False
                return True

            def do_POST(self) -> None:
                if not self._authorized():
                    return
                key = self.headers.get("Idempotency-Key", "")
                receiver.requests.append(("POST", key))
                length = int(self.headers.get("Content-Length", "0"))
                if not key or not 0 <= length <= 65536 or self.path != "/action":
                    self._reply(400, {"invalid": True})
                    return
                body = json.loads(self.rfile.read(length)) if length else None
                digest = receiver_request_digest("POST", receiver.business_endpoint, body)
                with closing(sqlite3.connect(receiver.database, timeout=10)) as connection:
                    with connection:
                        connection.execute("BEGIN IMMEDIATE")
                        old = connection.execute(
                            "SELECT request_digest,status,response FROM commits "
                            "WHERE operation_id=?",
                            (key,),
                        ).fetchone()
                        if old is not None:
                            if old[0] != digest:
                                self._reply(409, {"request_changed": True})
                                return
                            status, output = old[1], json.loads(old[2])
                        else:
                            output = (
                                None
                                if receiver.outcome == "null"
                                else {"committed": True, "operation_id": key}
                            )
                            status = 503 if receiver.outcome == "fault" else 200
                            if status == 503:
                                output = {"rejected": True}
                            error = (
                                {
                                    "code": "tool_error",
                                    "message": "HTTP request failed with an error status",
                                    "details": {
                                        "status": 503,
                                        "type": "https://open-workflow-specification.org/dsl/errors/types/communication",
                                        "title": "Service Unavailable",
                                        "detail": json.dumps(output),
                                    },
                                }
                                if status == 503
                                else None
                            )
                            proof = {
                                "profile": HTTP_RECEIVER_PROFILE,
                                "receiver_id": RECEIVER_ID,
                                "contract_version": "1.0.0",
                                "principal_reference": PRINCIPAL,
                                "operation_id": key,
                                "wire_request_digest": digest,
                                "status": "faulted" if error else "completed",
                                "output": None if error else output,
                                "error": error,
                                "error_status_code": 500 if error else None,
                            }
                            connection.execute(
                                "INSERT INTO commits VALUES (?,?,?,?,?)",
                                (key, digest, json.dumps(proof), status, json.dumps(output)),
                            )
                receiver.committed.set()
                if receiver.hold_response:
                    receiver.release.wait(120)
                self._reply(status, output)

            def do_GET(self) -> None:
                if not self._authorized():
                    return
                parsed = urlsplit(self.path)
                key = parse_qs(parsed.query).get("operation_id", [""])[0]
                receiver.requests.append(("GET", key))
                if parsed.path != "/outcome":
                    self._reply(404, {"status": "unknown"})
                    return
                receiver.lookup_started.set()
                if receiver.lookup_barrier is not None:
                    receiver.lookup_barrier.wait(30)
                if receiver.lookup_status != 200:
                    self._reply(receiver.lookup_status, {"status": "unknown"})
                    return
                if receiver.replacement_proof is not None:
                    self._reply(200, receiver.replacement_proof)
                    return
                with closing(sqlite3.connect(receiver.database)) as connection:
                    row = connection.execute(
                        "SELECT proof FROM commits WHERE operation_id=?", (key,)
                    ).fetchone()
                self._reply(
                    200 if row else 404, json.loads(row[0]) if row else {"status": "unknown"}
                )

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(self.certificate), str(self.private_key))
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        origin = f"https://127.0.0.1:{self.server.server_port}"
        self.business_endpoint, self.lookup_endpoint = origin + "/action", origin + "/outcome"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def proof(self, key: str) -> dict[str, Any]:
        with closing(sqlite3.connect(self.database)) as connection:
            row = connection.execute(
                "SELECT proof FROM commits WHERE operation_id=?", (key,)
            ).fetchone()
        assert row is not None
        return json.loads(row[0])

    def commits(self) -> int:
        with closing(sqlite3.connect(self.database)) as connection:
            return connection.execute("SELECT COUNT(*) FROM commits").fetchone()[0]

    def close(self) -> None:
        self.release.set()
        if self.lookup_barrier is not None:
            self.lookup_barrier.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        assert not self.thread.is_alive()


@contextmanager
def receiver(directory: Path, **kwargs):
    value = Receiver(directory, **kwargs)
    try:
        yield value
    finally:
        value.close()
