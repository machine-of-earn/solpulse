"""Solana JSON-RPC client (stdlib only).

Supports single and batched calls. A batch is one HTTP round-trip, which keeps
us comfortably inside the rate limits of the free public endpoint.
"""

from __future__ import annotations

from typing import Any, Callable, Sequence

from .http import HttpError, post_json

MAINNET_BETA = "https://api.mainnet-beta.solana.com"


class RpcError(Exception):
    """The node was reached but refused or failed the call."""


class RpcClient:
    """A thin Solana JSON-RPC client.

    `transport` is the function that actually performs the POST. Injecting it
    keeps the tests offline and deterministic.
    """

    def __init__(
        self,
        endpoint: str = MAINNET_BETA,
        timeout: float = 20.0,
        transport: Callable[[str, object, float], Any] | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.timeout = timeout
        self._transport = transport or post_json
        self._next_id = 1

    def _ids(self, count: int) -> list[int]:
        start = self._next_id
        self._next_id += count
        return list(range(start, start + count))

    def call(self, method: str, params: Sequence[Any] | None = None) -> Any:
        """Invoke one RPC method and return its `result`."""
        return self.batch([(method, params)])[0]

    def batch(self, calls: Sequence[tuple[str, Sequence[Any] | None]]) -> list[Any]:
        """Invoke several methods in one request, results in the order given.

        Raises RpcError if the node errors on *any* call in the batch. Callers
        that want per-source resilience should batch by source, not globally.
        """
        if not calls:
            return []

        ids = self._ids(len(calls))
        payload = [
            {"jsonrpc": "2.0", "id": rid, "method": method, "params": list(params or [])}
            for rid, (method, params) in zip(ids, calls)
        ]

        try:
            raw = self._transport(self.endpoint, payload, self.timeout)
        except HttpError as exc:
            raise RpcError(str(exc)) from exc

        if not isinstance(raw, list):
            raise RpcError(f"expected a batch response, got {type(raw).__name__}")
        if len(raw) != len(calls):
            raise RpcError(f"expected {len(calls)} responses, got {len(raw)}")

        by_id = {}
        for item in raw:
            if not isinstance(item, dict) or "id" not in item:
                raise RpcError(f"malformed response entry: {item!r}")
            by_id[item["id"]] = item

        results = []
        for rid, (method, _params) in zip(ids, calls):
            item = by_id.get(rid)
            if item is None:
                raise RpcError(f"no response for {method} (id {rid})")
            if "error" in item:
                err = item["error"] or {}
                raise RpcError(f"{method}: {err.get('message', err)!r} (code {err.get('code')})")
            if "result" not in item:
                raise RpcError(f"{method}: response has neither result nor error")
            results.append(item["result"])
        return results
