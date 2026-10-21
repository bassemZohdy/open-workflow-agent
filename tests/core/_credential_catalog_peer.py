"""Finite authenticated TLS metadata oracle; no remote source or paid service."""

from __future__ import annotations

import ssl
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from _auth_tls_fixture import auth_tls_receiver


@contextmanager
def credential_catalog_peer(directory, secret):
    with auth_tls_receiver(directory) as certificates:
        state = {"body": b"", "requests": [], "etag": "controlled-function"}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                supplied = self.headers.get("Authorization")
                state["requests"].append((self.path, supplied))
                valid = supplied == "Bearer " + secret
                body = state["body"] if valid else b"unauthorized"
                self.send_response(200 if valid else 403)
                self.send_header("Content-Type", "application/yaml")
                self.send_header("ETag", state["etag"])
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(*certificates.certificates["localhost"])
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield state, f"https://localhost:{server.server_port}", certificates.ca_bundle
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
            assert not thread.is_alive(), "controlled metadata peer did not stop"
