#!/usr/bin/env python3
"""
Serve the viewer page, and nothing else.

Why not `python3 -m http.server`: that publishes the whole working tree to
anyone who guesses a path -- including measurement data, the deployment notes
and any stray copy of the secrets file.  This server answers only for the files
the browser actually needs.

    python3 serve_page.py [port]        # default 8000

Running it as a real handler (rather than http.server) also gives somewhere to
put the time-limited TURN credential endpoint that the public deployment needs,
so the browser never has to be given a long-lived secret.
"""

import http.server
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

# Only these paths are served, and only these files.
ALLOWED = {
    "/": "index_latency.html",
    "/index.html": "index_latency.html",
    "/index_latency.html": "index_latency.html",
    "/config.js": "config.js",
}

DEFAULT_PORT = 8000


class Handler(http.server.BaseHTTPRequestHandler):

    server_version = "dsh-page/1.0"

    def do_GET(self):  # noqa: N802  (name fixed by BaseHTTPRequestHandler)
        path = self.path.split("?", 1)[0]

        name = ALLOWED.get(path)

        if name is None:
            self.send_error(404, "not found")
            return

        full = os.path.join(ROOT, name)

        if not os.path.isfile(full):
            self.send_error(404, "not found")
            return

        with open(full, "rb") as handle:
            body = handle.read()

        if name.endswith(".html"):
            content_type = "text/html; charset=utf-8"
        elif name.endswith(".js"):
            content_type = "application/javascript; charset=utf-8"
        else:
            content_type = "application/octet-stream"

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # No caching: a stale index_latency.html after a deploy is a confusing
        # way to lose an hour.  The payload is ~60 KB, so this is cheap.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        sys.stderr.write("[PAGE] " + (fmt % args) + "\n")


def main():
    port = DEFAULT_PORT
    if len(sys.argv) > 1:
        port = int(sys.argv[1])
    else:
        port = int(os.environ.get("DSH_PAGE_PORT", DEFAULT_PORT))

    server = http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler)

    print(
        "[PAGE] serving %s on 0.0.0.0:%d"
        % (sorted(set(ALLOWED.values())), port),
        flush=True,
    )

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
