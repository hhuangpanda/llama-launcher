"""Stand-in for llama-server in integration tests (stdlib only).

Controls via env: FAKE_DELAY (seconds before /health is 200), FAKE_EXIT ("code:line" to print a line
and exit before ready), FAKE_CRASH_AFTER (seconds after ready to exit 3),
FAKE_BUSY_SECONDS (/metrics reports a request processing for this long after each POST)."""

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).parent


def arg(name, default=None):
    a = sys.argv
    return a[a.index(name) + 1] if name in a else default


def main():
    if "--version" in sys.argv:
        print((HERE / "help" / "release-version.txt").read_text())
        return 0
    if "--help" in sys.argv:
        print((HERE / "help" / "release-help.txt").read_text(encoding="utf-8"))
        return 0
    out = sys.stdout
    print(f"0.00.000.001 I fake: argv {sys.argv[1:]}", file=out)
    print(f"0.00.000.002 I fake: api key {'set' if os.environ.get('LLAMA_API_KEY') else 'unset'}", file=out)
    print(f"0.00.000.003 I fake: cwd {os.getcwd()}", file=out, flush=True)
    if os.environ.get("FAKE_EXIT"):
        code, line = os.environ["FAKE_EXIT"].split(":", 1)
        time.sleep(0.2)
        print(line, file=out, flush=True)
        return int(code)

    key = os.environ.get("LLAMA_API_KEY", "")
    sleep_after = float(arg("--sleep-idle-seconds", "-1"))
    t0 = time.monotonic()
    ready_at = t0 + float(os.environ.get("FAKE_DELAY", "0"))
    state = {"last": time.monotonic(), "sleeping": False, "tasks": 0}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authed(self):
            return not key or self.headers.get("Authorization") == f"Bearer {key}"

        def do_GET(self):
            now = time.monotonic()
            if sleep_after > 0 and now - state["last"] > sleep_after:
                state["sleeping"] = True
            if self.path == "/health":
                return self._send(200 if now >= ready_at else 503, {"status": "ok" if now >= ready_at else "loading"})
            if not self._authed():
                return self._send(401, {"error": "Invalid API Key"})
            if self.path == "/props":
                return self._send(
                    200,
                    {
                        "is_sleeping": state["sleeping"],
                        "model_alias": arg("--alias"),
                        "default_generation_settings": {"n_ctx": int(arg("--ctx-size", "4096"))},
                    },
                )
            if self.path == "/metrics" and "--metrics" in sys.argv:
                busy = int(now - state["last"] < float(os.environ.get("FAKE_BUSY_SECONDS", "0")))
                body = f"llamacpp:requests_processing {busy}\nllamacpp:requests_deferred 0\n".encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                return self.wfile.write(body)
            if self.path == "/v1/models":
                return self._send(200, {"data": [{"id": arg("--alias", arg("--model"))}]})
            self._send(404, {})

        def do_POST(self):
            if not self._authed():
                return self._send(401, {"error": "Invalid API Key"})
            state.update(last=time.monotonic(), sleeping=False, tasks=state["tasks"] + 1)
            self._send(200, {"choices": [{"message": {"content": "hi"}}]})

    ThreadingHTTPServer.allow_reuse_address = False
    srv = ThreadingHTTPServer((arg("--host", "127.0.0.1"), int(arg("--port", "9931"))), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(
        f"0.00.100.000 I srv  llama_server: listening on http://{arg('--host')}:{arg('--port')}", file=out, flush=True
    )
    crash = os.environ.get("FAKE_CRASH_AFTER")
    while True:
        time.sleep(0.1)
        if crash and time.monotonic() - ready_at > float(crash):
            print("0.09.000.000 E srv  fake: crashing", file=out, flush=True)
            os._exit(3)


if __name__ == "__main__":
    sys.exit(main())
