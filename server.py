"""Snap Answer: phone camera -> PC -> Claude Code CLI -> answer back to the phone.

Run:  python server.py   then open the printed http://<PC-IP>:8765 on your phone (same Wi-Fi).
Uses the `claude` CLI you are already logged into, so no API key is needed.
"""
import json
import shutil
import socket
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PORT = 8765
ROOT = Path(__file__).resolve().parent
SHOTS = ROOT / "shots"
SHOTS.mkdir(exist_ok=True)
CLAUDE = shutil.which("claude") or "claude"
MODELS = {"fast": "haiku", "smart": "opus"}

PROMPT = """Read the image file at: {path}
It is a phone photo of a computer or TV screen showing a game. Ignore glare, moire and the screen bezel.
Find the question, puzzle or prompt on screen and answer it.
Reply with exactly two lines and nothing else:
Line 1: ONLY the answer, as short as possible (if there are lettered/numbered options, give the label and text, e.g. "B) Saturn").
Line 2: one short sentence of reasoning.
If you can't read the screen, say so on line 1 and what to fix (closer, less glare, steadier) on line 2.{note}"""


def ask_claude(img: Path, mode: str, note: str) -> str:
    prompt = PROMPT.format(path=img, note=f"\n\nExtra instructions from the player: {note}" if note else "")
    cmd = [
        CLAUDE, "-p", prompt,
        "--model", MODELS.get(mode, "haiku"),
        "--tools", "Read", "--allowedTools", "Read",
        "--strict-mcp-config", "--no-session-persistence",
        "--output-format", "text",
    ]
    out = subprocess.run(cmd, cwd=SHOTS, capture_output=True, text=True, encoding="utf-8", timeout=180)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or out.stdout.strip() or "claude CLI failed")
    return out.stdout.strip()


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if urlparse(self.path).path in ("/", "/index.html"):
            self._send(200, (ROOT / "index.html").read_bytes(), "text/html; charset=utf-8")
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        url = urlparse(self.path)
        if url.path != "/ask":
            return self._send(404, json.dumps({"error": "not found"}))
        q = parse_qs(url.query)
        mode = q.get("mode", ["fast"])[0]
        note = q.get("note", [""])[0][:500]
        length = int(self.headers.get("Content-Length", 0))
        if not 0 < length <= 15_000_000:
            return self._send(400, json.dumps({"error": "Photo missing or too large."}))
        img = SHOTS / f"shot_{time.strftime('%Y%m%d_%H%M%S')}_{int(time.time() * 1000) % 1000:03d}.jpg"
        img.write_bytes(self.rfile.read(length))
        t0 = time.time()
        try:
            answer = ask_claude(img, mode, note)
            secs = round(time.time() - t0, 1)
            print(f"[{mode} {secs}s] {answer.splitlines()[0] if answer else ''}")
            self._send(200, json.dumps({"answer": answer, "seconds": secs}))
        except subprocess.TimeoutExpired:
            self._send(504, json.dumps({"error": "Claude took too long. Try again."}))
        except Exception as e:
            print("error:", e)
            self._send(500, json.dumps({"error": str(e)[:400]}))

    def log_message(self, fmt, *args):
        pass


def lan_ips():
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
        ips.add(info[4][0])
    return sorted(ip for ip in ips if not ip.startswith(("127.", "169.254.")))


if __name__ == "__main__":
    print("Snap Answer running. Open on your phone (same Wi-Fi):")
    for ip in lan_ips():
        print(f"   http://{ip}:{PORT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
