#!/usr/bin/env python3
"""Verify a fresh VM can actually run the watchers before you trust it.

Checks everything that can silently fail on a headless box: Python version,
outbound HTTPS to each ATS, the startup.jobs MCP endpoint, and - the one Oracle
Cloud is known to interfere with - outbound SMTP.

  python3 deploy/preflight.py            checks only
  python3 deploy/preflight.py --send     also sends a real test email
"""

import json
import pathlib
import smtplib
import socket
import ssl
import sys
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

OK, BAD, WARN = "  [ok]  ", "  [FAIL]", "  [warn]"
failures = []


def check(label, fn, fatal=True):
    try:
        detail = fn()
        print(f"{OK} {label}" + (f" - {detail}" if detail else ""))
        return True
    except Exception as exc:
        print(f"{BAD if fatal else WARN} {label} - {type(exc).__name__}: {exc}")
        if fatal:
            failures.append(label)
        return False


def py_version():
    if sys.version_info < (3, 9):
        raise RuntimeError(f"need >= 3.9, found {sys.version.split()[0]}")
    return sys.version.split()[0]


def https(url, name):
    def go():
        req = urllib.request.Request(url, headers={"User-Agent": "preflight"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return f"HTTP {r.status}"
    return lambda: go()


def port_open(host, port):
    def go():
        with socket.create_connection((host, port), timeout=12):
            return f"{host}:{port} reachable"
    return go


def smtp_login(cfg, port, mode):
    def go():
        import main
        pw = main.get_secret(cfg, "gmail_app_password")
        addr = main.get_secret(cfg, "alert_email_from") or main.get_secret(cfg, "alert_email_to")
        if not pw or not addr:
            raise RuntimeError("no credentials (set secrets.json or env vars)")
        ctx = ssl.create_default_context()
        host = cfg["email"]["smtp_host"]
        srv = (smtplib.SMTP_SSL(host, port, context=ctx, timeout=25) if mode == "ssl"
               else smtplib.SMTP(host, port, timeout=25))
        with srv:
            if mode == "starttls":
                srv.starttls(context=ctx)
            srv.login(addr, pw)
        return f"authenticated on {port}"
    return go


def main_check():
    print("=== preflight ===\n")
    check("python >= 3.9", py_version)

    print()
    for name, url in [
        ("greenhouse", "https://boards-api.greenhouse.io/v1/boards/stripe/jobs"),
        ("ashby", "https://api.ashbyhq.com/posting-api/job-board/cohere"),
        ("lever", "https://api.lever.co/v0/postings/waabi?mode=json"),
    ]:
        check(f"outbound HTTPS -> {name}", https(url, name))

    def mcp():
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                           "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                      "clientInfo": {"name": "preflight", "version": "1"}}}).encode()
        req = urllib.request.Request(
            "https://api.startup.jobs/mcp", data=body,
            headers={"Content-Type": "application/json",
                     "Accept": "application/json, text/event-stream"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return f"HTTP {r.status}"
    check("outbound HTTPS -> startup.jobs MCP", mcp)

    print()
    cfg_path = HERE / "config.json"
    if not cfg_path.exists():
        print(f"{BAD} config.json missing")
        failures.append("config.json")
        return
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    host = cfg["email"]["smtp_host"]

    # Oracle Cloud blocks port 25 outright and can throttle submission ports.
    p465 = check("SMTP port 465 open", port_open(host, 465), fatal=False)
    p587 = check("SMTP port 587 open", port_open(host, 587), fatal=False)
    if not (p465 or p587):
        print(f"{BAD} no SMTP port reachable - email cannot be delivered from this host")
        failures.append("SMTP connectivity")

    if p465:
        check("SMTP auth on 465", smtp_login(cfg, 465, "ssl"), fatal=False)
    if p587:
        check("SMTP auth on 587", smtp_login(cfg, 587, "starttls"), fatal=False)

    if "--send" in sys.argv:
        print()
        import main
        ok = main.send_email(cfg, "[preflight] intern alerts on this host",
                             "If you are reading this, the VM can send mail.",
                             "<p>If you are reading this, the VM can send mail.</p>")
        print(f"{OK if ok else BAD} test email sent: {ok}")

    print()
    if failures:
        print(f"FAILED: {', '.join(failures)}")
        sys.exit(1)
    print("all critical checks passed")


if __name__ == "__main__":
    main_check()
