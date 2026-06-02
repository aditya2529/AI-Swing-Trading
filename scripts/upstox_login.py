"""Generate a fresh Upstox access token and write it into .env.

WHY THIS EXISTS
---------------
Upstox API keys/secrets are permanent, but the ACCESS TOKEN expires every
day (~03:30 IST). So before each day's backfill you need a new token. This
script automates the OAuth dance:

    python scripts/upstox_login.py

It will:
  1. Read UPSTOX_ENV + the matching UPSTOX_{ENV}_API_KEY / _API_SECRET and
     UPSTOX_REDIRECT_URI from .env.
  2. Print a login URL — open it in your browser, log in to Upstox, approve.
  3. Your browser redirects to the (localhost) redirect URI; it won't load
     a page, but the address bar will read  ...callback?code=XXXXXXXX.
     Copy that whole address (or just the code) and paste it back here.
  4. The script exchanges the code for an access token and writes
     UPSTOX_{ENV}_ACCESS_TOKEN into .env. (The token value is never printed.)

Then run the backfill, e.g.:  python main.py backfill --source upstox
"""
from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"

AUTH_URL = "https://api.upstox.com/v2/login/authorization/dialog"
TOKEN_URL = "https://api.upstox.com/v2/login/authorization/token"


def _fail(msg: str) -> "NoReturn":  # type: ignore[name-defined]
    print(f"\n[ERROR] {msg}")
    sys.exit(1)


def _extract_code(raw: str) -> str:
    """Accept either a bare code or the full redirected URL."""
    raw = raw.strip()
    if "code=" in raw:
        qs = parse_qs(urlparse(raw).query)
        if "code" in qs and qs["code"]:
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

    login = (f"{AUTH_URL}?response_type=code&client_id={api_key}"
             f"&redirect_uri={redirect}")
    print(f"\nUpstox env: {mode}")
    print("\n1) Open this URL in your browser, log in, and approve:\n")
    print(f"   {login}\n")
    print("2) Your browser will redirect to the localhost URI (it won't load "
          "a page —\n   that's fine). Copy the address-bar URL (it contains "
          "?code=...).\n")

    pasted = input("3) Paste the redirected URL (or just the code) here: ").strip()
    code = _extract_code(pasted)
    if not code:
        _fail("Could not read an authorization code from that input.")

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
