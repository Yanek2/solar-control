"""
Monitoring dashboard — runs in a background thread inside the main process.
Access: http://<host>:8080/   (basic auth, credentials from DASH_USER / DASH_PASS env vars)
"""

import functools
import json
import os
from datetime import datetime
from pathlib import Path

from flask import Flask, Response, request

app = Flask(__name__)

_BASE = Path(__file__).parent
STATUS_FILE = _BASE / "status.json"
LOG_FILE = _BASE / "solar_control.log"

DASH_USER = os.environ.get("DASH_USER", "admin")
DASH_PASS = os.environ.get("DASH_PASS", "")


def _require_auth(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        if not DASH_PASS:
            return Response("Set DASH_PASS env var to enable dashboard", 503)
        auth = request.authorization
        if not auth or auth.username != DASH_USER or auth.password != DASH_PASS:
            return Response(
                "Authentication required",
                401,
                {"WWW-Authenticate": 'Basic realm="Solar Control"'},
            )
        return f(*args, **kwargs)
    return wrapper


@app.route("/")
@_require_auth
def index():
    status = {}
    if STATUS_FILE.exists():
        try:
            status = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass

    log_lines = []
    if LOG_FILE.exists():
        try:
            lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
            log_lines = lines[-50:]
        except Exception:
            pass

    return _render(status, log_lines)


@app.route("/status.json")
@_require_auth
def status_json():
    if STATUS_FILE.exists():
        return Response(STATUS_FILE.read_bytes(), mimetype="application/json")
    return Response("{}", mimetype="application/json")


def _render(status: dict, log_lines: list[str]) -> str:
    mode = status.get("mode", "unknown")
    price = status.get("price")
    threshold = status.get("threshold", 15.0)
    timestamp = status.get("timestamp", "—")
    error = status.get("error")

    stale = True
    if timestamp and timestamp != "—":
        try:
            age = (datetime.now() - datetime.fromisoformat(timestamp)).total_seconds()
            stale = age > 900  # 15 min
        except Exception:
            pass

    if mode == "zero_export":
        mode_label, mode_color = "ZERO EXPORT", "#e74c3c"
    elif mode == "no_limit":
        mode_label, mode_color = "NO LIMIT", "#2ecc71"
    else:
        mode_label, mode_color = "UNKNOWN", "#888"

    price_str = f"{price:.2f} EUR/MWh" if price is not None else "—"

    log_html = "".join(
        f'<div class="ll">{line}</div>' for line in log_lines
    )

    stale_html = (
        '<div class="banner warn">⚠ Last check over 15 min ago — service may be down</div>'
        if stale else ""
    )
    error_html = (
        f'<div class="banner err">Last error: {error}</div>'
        if error else ""
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="60">
<title>Solar Monitor</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f1117;color:#e0e0e0;padding:24px}}
h1{{font-size:1.3rem;font-weight:600;color:#fff;margin-bottom:18px}}
.banner{{padding:10px 16px;border-radius:8px;margin-bottom:14px;font-size:.88rem}}
.warn{{background:#7f4f00;color:#ffe08a}}
.err{{background:#4a0000;color:#ff9a9a;font-family:monospace;font-size:.82rem}}
.cards{{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:22px}}
.card{{background:#1a1d27;border-radius:10px;padding:18px 22px;flex:1;min-width:160px}}
.cl{{font-size:.72rem;text-transform:uppercase;letter-spacing:.08em;color:#888;margin-bottom:6px}}
.mode{{font-size:2rem;font-weight:700;color:{mode_color}}}
.big{{font-size:1.7rem;font-weight:600;color:#fff}}
.sm{{font-size:.95rem;color:#ccc}}
.logs h2{{font-size:.78rem;text-transform:uppercase;letter-spacing:.08em;color:#888;margin-bottom:8px}}
.box{{background:#1a1d27;border-radius:10px;padding:14px;max-height:420px;overflow-y:auto;font-family:'Courier New',monospace;font-size:.76rem;line-height:1.6}}
.ll{{padding:1px 0;border-bottom:1px solid #23262f;white-space:pre-wrap;word-break:break-all}}
.ll:last-child{{border-bottom:none}}
.foot{{margin-top:12px;font-size:.72rem;color:#555;text-align:right}}
</style>
</head>
<body>
<h1>Solar Export Control</h1>
{stale_html}{error_html}
<div class="cards">
  <div class="card"><div class="cl">Mode</div><div class="mode">{mode_label}</div></div>
  <div class="card"><div class="cl">IBEX Price</div><div class="big">{price_str}</div></div>
  <div class="card"><div class="cl">Threshold</div><div class="sm">{threshold:.1f} EUR/MWh</div></div>
  <div class="card"><div class="cl">Last Check</div><div class="sm">{timestamp}</div></div>
</div>
<div class="logs">
  <h2>Recent Log</h2>
  <div class="box" id="lb">{log_html}</div>
</div>
<div class="foot">Auto-refreshes every 60 s</div>
<script>var b=document.getElementById('lb');b.scrollTop=b.scrollHeight;</script>
</body>
</html>"""


def start(port: int = 8080) -> None:
    import logging
    log = logging.getLogger("werkzeug")
    log.setLevel(logging.ERROR)
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
