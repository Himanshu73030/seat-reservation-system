import argparse
import json
import os
import re
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
SHOW_ID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


class FrontendHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_GET(self):
        if self.path.startswith("/api/"):
            self._proxy("GET")
            return
        if self.path == "/":
            self.path = "/index.html"
        super().do_GET()

    def do_POST(self):
        self._proxy("POST")

    def _proxy(self, method):
        if self.path == "/api/shows":
            show_id, reservation_id = None, None
            suffix = "/shows"
        else:
            match = re.fullmatch(
                r"/api/shows/([^/]+)(?:/reserve)?|/api/reservations/([^/]+)/cancel",
                self.path,
            )
            if not match:
                self._send_json(404, {"detail": "frontend_proxy_route_not_found"})
                return
            show_id, reservation_id = match.groups()
            suffix = self.path.removeprefix("/api")
        if show_id and not SHOW_ID.fullmatch(show_id):
            self._send_json(400, {"detail": "invalid_show_id"})
            return
        if reservation_id and not SHOW_ID.fullmatch(reservation_id):
            self._send_json(400, {"detail": "invalid_reservation_id"})
            return

        if suffix == "/shows" and method != "POST":
            self._send_json(405, {"detail": "method_not_allowed"})
            return
        if method == "GET" and suffix.endswith("/reserve"):
            self._send_json(405, {"detail": "method_not_allowed"})
            return
        if method == "POST" and (suffix.endswith("/reserve") or suffix.endswith("/cancel")):
            pass
        elif method == "POST" and suffix == "/shows":
            pass
        elif method == "GET" and re.fullmatch(r"/shows/[^/]+", suffix):
            pass
        else:
            self._send_json(405, {"detail": "method_not_allowed"})
            return

        body = self.rfile.read(int(self.headers.get("Content-Length", "0"))) if method == "POST" else None
        headers = {
            name: self.headers[name]
            for name in ("Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID")
            if self.headers.get(name)
        }
        request = Request(
            f"{os.getenv('API_BASE_URL', 'http://127.0.0.1:8000').rstrip('/')}{suffix}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=10) as response:
                payload = response.read()
                self.send_response(response.status)
                self.send_header("Content-Type", response.headers.get("Content-Type", "application/json"))
                if response.headers.get("Idempotency-Replayed"):
                    self.send_header("Idempotency-Replayed", response.headers["Idempotency-Replayed"])
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
        except HTTPError as error:
            payload = error.read()
            self.send_response(error.code)
            self.send_header("Content-Type", error.headers.get("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except (TimeoutError, URLError) as error:
            self._send_json(502, {"detail": "api_unavailable", "message": str(error.reason if isinstance(error, URLError) else error)})

    def _send_json(self, status, payload):
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format_string, *args):
        print(f"[frontend] {self.address_string()} {format_string % args}")


def main():
    parser = argparse.ArgumentParser(description="Serve the seat map and proxy its API requests.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4173)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), FrontendHandler)
    print(f"Seat map: http://{args.host}:{args.port} (API: {os.getenv('API_BASE_URL', 'http://127.0.0.1:8000')})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping seat map server")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()