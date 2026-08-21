"""Minimal stdlib HTTP/JSON helpers.

Deliberately tiny: `urllib.request` with an explicit timeout, a real
User-Agent, and errors raised as a single typed exception so callers never
have to distinguish urllib's error zoo.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

USER_AGENT = "solpulse/0.1 (+https://github.com/)"
DEFAULT_TIMEOUT = 20.0


class HttpError(Exception):
    """Any failure while fetching or decoding an HTTP JSON response."""


def _open(req: urllib.request.Request, timeout: float) -> bytes:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:  # 4xx/5xx
        raise HttpError(f"HTTP {exc.code} for {req.full_url}") from exc
    except urllib.error.URLError as exc:  # DNS, TLS, connection refused
        raise HttpError(f"connection failed for {req.full_url}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise HttpError(f"timed out after {timeout}s for {req.full_url}") from exc


def get_json(url: str, timeout: float = DEFAULT_TIMEOUT) -> object:
    """GET `url` and decode the body as JSON."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    raw = _open(req, timeout)
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise HttpError(f"invalid JSON from {url}: {exc}") from exc


def post_json(url: str, payload: object, timeout: float = DEFAULT_TIMEOUT) -> object:
    """POST `payload` as JSON to `url` and decode the JSON response."""
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    raw = _open(req, timeout)
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise HttpError(f"invalid JSON from {url}: {exc}") from exc
