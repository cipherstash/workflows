#!/usr/bin/env python3
"""Catches the one-time code of GitHub's app manifest flow, for scripts/setup-organisation.sh.

Usage: manifest_listener.py <org> < manifest.json

Listens on 127.0.0.1, on a free port, and prints the port on its first line. The address http://127.0.0.1:<port>/
serves a page that posts the manifest (read from stdin, with its redirect_url set to this listener) to GitHub's "new
app" page for the organisation. After "Create GitHub App", GitHub sends the browser back to /callback with a code and
the state the page sent; the listener prints the code on its second line and exits 0. It exits 1, printing nothing
more, if no callback with the right state comes within ten minutes.

The code is good for one hour and only once: setup-organisation.sh turns it into the app's client id and private key.
Nothing else is read from or written to disk.
"""

import html
import http.server
import json
import secrets
import sys
import time
import urllib.parse
from typing import Any

TIMEOUT_SECONDS = 600


class Listener(http.server.HTTPServer):
    page: bytes
    state: str
    code: str | None = None


class Handler(http.server.BaseHTTPRequestHandler):
    server: Listener

    def do_GET(self) -> None:
        url = urllib.parse.urlsplit(self.path)
        if url.path == "/":
            self.reply(200, self.server.page)
            return
        if url.path == "/callback":
            query = urllib.parse.parse_qs(url.query)
            code = query.get("code", [""])[0]
            state = query.get("state", [""])[0]
            if code and code.replace("-", "").replace("_", "").isalnum() and secrets.compare_digest(
                state, self.server.state
            ):
                self.server.code = code
                self.reply(200, page("Done", "<p>GitHub created the app. Go back to the terminal.</p>"))
                return
            self.reply(400, page("Not this one", "<p>This address is not the one GitHub was asked to call.</p>"))
            return
        self.reply(404, page("Not found", ""))

    def reply(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        """Keeps the terminal quiet: the script says what is happening."""


def page(title: str, body: str) -> bytes:
    return f"<!doctype html><meta charset=utf-8><title>{html.escape(title)}</title>{body}".encode()


def form_page(org: str, manifest: dict[str, Any], state: str) -> bytes:
    """A page that posts the manifest to the organisation's "new app" page as soon as it loads."""
    action = (
        f"https://github.com/organizations/{urllib.parse.quote(org, safe='')}/settings/apps/new"
        f"?state={urllib.parse.quote(state, safe='')}"
    )
    return page(
        "Create the app on GitHub",
        f'<form id="f" method="post" action="{html.escape(action)}">'
        f'<input type="hidden" name="manifest" value="{html.escape(json.dumps(manifest))}">'
        "<p>Sending you to GitHub. If nothing happens, press the button.</p>"
        '<button type="submit">Create the app on GitHub</button></form>'
        '<script>document.getElementById("f").submit()</script>',
    )


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: manifest_listener.py <org> < manifest.json", file=sys.stderr)
        return 2
    org = argv[1]
    manifest = json.load(sys.stdin)
    server = Listener(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    server.state = secrets.token_urlsafe(24)
    manifest["redirect_url"] = f"http://127.0.0.1:{port}/callback"
    server.page = form_page(org, manifest, server.state)
    print(port, flush=True)
    server.timeout = 1
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while server.code is None and time.monotonic() < deadline:
        server.handle_request()
    server.server_close()
    if server.code is None:
        return 1
    print(server.code, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
