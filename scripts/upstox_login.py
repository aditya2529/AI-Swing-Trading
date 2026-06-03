"""Generate a fresh Upstox access token and write it into .env.

WHY THIS EXISTS
---------------
Upstox API keys/secrets are permanent, but the ACCESS TOKEN expires every
day (~03:30 IST). So before each day's backfill you need a new token.

    python scripts/upstox_login.py

HOW IT WORKS (auto mode)
------------------------
1. It starts a tiny local web server on the redirect address
   (http://127.0.0.1:8000/upstox/callback) and opens the Upstox login URL
   in your browser.
2. You log in + approve on Upstox.
3. Upstox redirects back to the local server, which CATCHES the code
   automatically — no copy/paste. The browser shows "Success, you can
   close this tab."
4. The script exchanges the code for an access token and writes
   UPSTOX_{ENV}_ACCESS_TOKEN into .env (the token value is never printed).

If the local server can't start (e.g. port 8000 busy), it falls back to
MANUAL mode: it prints the login URL, you log in, then paste the
redirected address-bar URL back into the terminal.
"""
from __future__ import annotations

import sys
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"

AUTH_URL = "https://api.upstox.com/v2/login/authorization/dialog"
TOKEN_URL = "https://api.upstox.com/v2/login/authorization/token"


def _fail(msg: str):
    print(f"\n[ERROR] {msg}")
    sys.exit(1)


def _extract_code(raw: str) -> str:
    raw = raw.strip()
    if "code=" in raw:
        qs = parse_qs(urlparse(raw).query)
        if qs.get("code"):
            return qs["code"][0]
    return raw


def _set_env_var(key: str, value: str) -> None:
    """Insert or replace ``key=value`` in .env, preserving everything else."""
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    out, replaced = [], False
    for line in lines:
        if line.strip().startswith(f"{key}="):
            out.append(f"{key}={value}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")


# ── auto mode: a one-shot local server that catches the ?code= redirect ──

class _CallbackHandler(BaseHTTPRequestHandler):
    captured_code: str | None = None

    def do_GET(self):  # noqa: N802
        qs = parse_qs(urlparse(self.path).query)
        code = qs.get("code", [None])[0]
        if code:
            _CallbackHandler.captured_code = code
            body = (b"<html><body style='font-family:sans-serif'>"
                    b"<h2>Success.</h2><p>Token captured. You can close this "
                    b"tab and return to the terminal.</p></body></html>")
        else:
            body = (b"<html><body><h2>Waiting for Upstox redirect...</h2>"
                    b"</body></html>")
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence default request logging
        pass


def _get_code_auto(login_url: str, host: str, port: int) -> str | None:
    """Start the local callback server, open the browser, wait for the code.
    Returns the code, or None if the server couldn't bind (caller falls back)."""
    try:
        server = HTTPServer((host, port), _CallbackHandler)
    except OSError:
        return None
    server.timeout = 1.0  # handle_request returns each second so we can re-check
    print(f"\nLocal callback server listening on http://{host}:{port}")
    print("\nOpening the Upstox login in your browser. If it doesn't open, "
          "paste this URL into your browser manually:\n")
    print(f"   {login_url}\n")
    try:
        webbrowser.open(login_url)
    except Exception:
        pass
    print("Waiting for you to log in + approve in the browser "
          "(up to 5 minutes) ...")
    deadline = time.monotonic() + 300
    while _CallbackHandler.captured_code is None and time.monotonic() < deadline:
        server.handle_request()  # blocks up to server.timeout, then re-checks
    server.server_close()
    return _CallbackHandler.captured_code


def _get_code_manual(login_url: str) -> str:
    print("\n[manual mode] Open this URL, log in, and approve:\n")
    print(f"   {login_url}\n")
    print("Your browser redirects to a localhost page that won't load — copy "
          "that address-bar URL (it has ?code=...).\n")
    pasted = input("Paste the redirected URL (or just the code) here: ").strip()
    return _extract_code(pasted)


def main() -> int:
    if not ENV_PATH.exists():
        _fail(f"No .env at {ENV_PATH}. Copy .env.example to .env first.")

    env = dotenv_values(str(ENV_PATH))
    mode = (env.get("UPSTOX_ENV") or "").strip()
    if mode not in ("sandbox", "prod"):
        _fail("UPSTOX_ENV must be 'sandbox' or 'prod' in .env.")

    api_key = (env.get(f"UPSTOX_{mode.upper()}_API_KEY") or "").strip()
    api_secret = (env.get(f"UPSTOX_{mode.upper()}_API_SECRET") or "").strip()
    redirect = (env.get("UPSTOX_REDIRECT_URI") or "").strip()
    if not (api_key and api_secret and redirect):
        _fail(f"Missing UPSTOX_{mode.upper()}_API_KEY / _API_SECRET / "
              f"UPSTOX_REDIRECT_URI in .env.")

    parsed = urlparse(redirect)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 8000

    login = (f"{AUTH_URL}?response_type=code&client_id={api_key}"
             f"&redirect_uri={redirect}")
    print(f"Upstox env: {mode}")

    code = _get_code_auto(login, host, port)
    if not code:
        print("\n(Could not auto-capture — switching to manual paste.)")
        code = _get_code_manual(login)
    if not code:
        _fail("No authorization code received. Re-run and try again.")

    print("\nExchanging the code for an access token ...")
    resp = requests.post(
        TOKEN_URL,
        headers={"accept": "application/json",
                 "Content-Type": "application/x-www-form-urlencoded"},
        data={"code": code, "client_id": api_key, "client_secret": api_secret,
              "redirect_uri": redirect, "grant_type": "authorization_code"},
        timeout=30,
    )
    if resp.status_code != 200:
        _fail(f"Token exchange failed: HTTP {resp.status_code}: {resp.text[:300]}")

    payload = resp.json()
    token = payload.get("access_token") or (payload.get("data") or {}).get("access_token")
    if not token:
        _fail(f"No access_token in response: {str(payload)[:300]}")

    _set_env_var(f"UPSTOX_{mode.upper()}_ACCESS_TOKEN", token)
    print(f"\n[OK] Wrote UPSTOX_{mode.upper()}_ACCESS_TOKEN to .env "
          f"(length {len(token)}). Valid until ~03:30 IST tomorrow.")
    print("Next:  python main.py backfill --source upstox")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
