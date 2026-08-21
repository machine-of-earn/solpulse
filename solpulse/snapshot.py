"""Assemble one ecosystem snapshot from every source.

The core rule, inherited from a bug this project shipped once already: an
error is never recorded as a value. Each source lands in the snapshot as
`{"status": "ok", "data": {...}}` or `{"status": "error", "error": "..."}`.
A reader can therefore always tell "the number is zero" apart from "we could
not find out".
"""

from __future__ import annotations

import datetime
import time
from typing import Callable

from . import __version__
from .rpc import MAINNET_BETA, RpcClient
from .sources import economics, network, offchain, validators

# name -> zero-argument callable returning that source's dict.
SourceMap = dict[str, Callable[[], dict]]


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_sources(client: RpcClient) -> SourceMap:
    """The standard source set: three on-chain, two off-chain."""
    return {
        "network": lambda: network.collect(client),
        "validators": lambda: validators.collect(client),
        "economics": lambda: economics.collect(client),
        "price": offchain.collect_price,
        "tvl": offchain.collect_tvl,
    }


def collect_snapshot(sources: SourceMap, clock: Callable[[], float] = time.monotonic) -> dict:
    """Run every source, isolating failures, and return the snapshot dict."""
    results: dict[str, dict] = {}
    for name, fetch in sources.items():
        started = clock()
        try:
            data = fetch()
        except Exception as exc:  # a bad source must not sink the report
            results[name] = {
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
                "elapsed_ms": round((clock() - started) * 1000, 1),
            }
        else:
            results[name] = {
                "status": "ok",
                "data": data,
                "elapsed_ms": round((clock() - started) * 1000, 1),
            }

    ok = [n for n, r in results.items() if r["status"] == "ok"]
    failed = [n for n, r in results.items() if r["status"] == "error"]

    return {
        "schema": "solpulse/snapshot/v1",
        "generator": f"solpulse {__version__}",
        "generated_at": _utc_now(),
        "sources": results,
        "summary": {
            "sources_total": len(results),
            "sources_ok": len(ok),
            "sources_failed": len(failed),
            "failed_sources": failed,
            "complete": not failed,
        },
    }


def snapshot(endpoint: str = MAINNET_BETA, timeout: float = 20.0) -> dict:
    """Convenience entry point: live snapshot against a real RPC endpoint."""
    client = RpcClient(endpoint=endpoint, timeout=timeout)
    return collect_snapshot(default_sources(client))


def get(snap: dict, source: str, *path: str, default=None):
    """Read `snap["sources"][source]["data"][*path]`, or `default` if absent.

    Returns `default` for a failed source too, so renderers can print
    "unknown" without special-casing every lookup.
    """
    entry = (snap.get("sources") or {}).get(source) or {}
    if entry.get("status") != "ok":
        return default
    node = entry.get("data") or {}
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node if node is not None else default
