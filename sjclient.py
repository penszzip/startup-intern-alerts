"""Minimal Startup Jobs MCP client over Streamable HTTP (stdlib only)."""

import json
import itertools
import time
import urllib.error
import urllib.request

ENDPOINT = "https://api.startup.jobs/mcp"
_ids = itertools.count(1)


class SJError(RuntimeError):
    pass


def _parse_body(raw: str) -> dict:
    """Handle both plain JSON and text/event-stream framing."""
    raw = raw.strip()
    if raw.startswith("{"):
        return json.loads(raw)
    for line in raw.splitlines():
        if line.startswith("data:"):
            chunk = line[5:].strip()
            if chunk and chunk != "[DONE]":
                return json.loads(chunk)
    raise SJError(f"unparseable response: {raw[:200]}")


def rpc(method: str, params: dict, api_key: str | None = None, retries: int = 4) -> dict:
    payload = {"jsonrpc": "2.0", "id": next(_ids), "method": method, "params": params}
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "User-Agent": "startup-intern-alerts/1.0",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(
        ENDPOINT, data=json.dumps(payload).encode("utf-8"), headers=headers
    )
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = _parse_body(resp.read().decode("utf-8", "replace"))
            break
        except urllib.error.HTTPError as exc:
            # 429 = free-tier rate limit (20 req/min). Back off and retry.
            if exc.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(2 ** attempt * 3)
                continue
            raise SJError(f"HTTP {exc.code}: {exc.read()[:200]!r}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt < retries - 1:
                time.sleep(2 ** attempt * 3)
                continue
            raise SJError(f"network error: {exc}") from exc
    else:
        raise SJError("exhausted retries")

    if "error" in body:
        raise SJError(f"rpc error: {body['error']}")
    return body["result"]


def call_tool(name: str, arguments: dict, api_key: str | None = None) -> dict:
    result = rpc("tools/call", {"name": name, "arguments": arguments}, api_key)
    if result.get("isError"):
        raise SJError(f"{name} failed: {result['content'][0]['text'][:300]}")
    return json.loads(result["content"][0]["text"])


def search_jobs(api_key: str | None = None, max_pages: int = 8, **filters) -> list[dict]:
    """Paginate search_jobs, respecting the free tier's 20 req/min budget."""
    jobs, cursor = [], None
    for page in range(max_pages):
        args = {k: v for k, v in filters.items() if v is not None}
        args.setdefault("limit", 50)
        if cursor:
            args["cursor"] = cursor
        data = call_tool("search_jobs", args, api_key)
        jobs.extend(data.get("jobs", []))
        cursor = data.get("next_cursor")
        if not data.get("has_more") or not cursor:
            break
        time.sleep(3.2)  # stay under 20/min
    return jobs
