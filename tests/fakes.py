"""Offline stand-ins for the network. Tests never touch the real internet."""

from __future__ import annotations

from solpulse.http import HttpError


def rpc_transport(responses: dict, *, fail_with: Exception | None = None):
    """Build a transport that answers a batch from a {method: result} map.

    `responses[method]` may be a plain result, or an `{"error": {...}}` dict to
    make that one call fail the way a real node would.
    """
    calls: list[list[dict]] = []

    def transport(endpoint: str, payload: object, timeout: float):
        if fail_with is not None:
            raise fail_with
        calls.append(payload)
        out = []
        for item in payload:
            method = item["method"]
            if method not in responses:
                raise AssertionError(f"test asked for unstubbed method {method}")
            value = responses[method]
            if isinstance(value, dict) and "error" in value and len(value) == 1:
                out.append({"jsonrpc": "2.0", "id": item["id"], "error": value["error"]})
            else:
                out.append({"jsonrpc": "2.0", "id": item["id"], "result": value})
        return out

    transport.calls = calls
    return transport


def http_error(message: str = "boom"):
    """A getter that always fails, for testing source isolation."""

    def getter(*_args, **_kwargs):
        raise HttpError(message)

    return getter


PERF_SAMPLES = [
    {"slot": 1000, "numSlots": 150, "numTransactions": 240000, "numNonVoteTransactions": 120000, "samplePeriodSecs": 60},
    {"slot": 850, "numSlots": 150, "numTransactions": 180000, "numNonVoteTransactions": 90000, "samplePeriodSecs": 60},
]

EPOCH_INFO = {
    "absoluteSlot": 440580196,
    "blockHeight": 418629925,
    "epoch": 1019,
    "slotIndex": 216000,
    "slotsInEpoch": 432000,
    "transactionCount": 540156063043,
}

VOTE_ACCOUNTS = {
    "current": [
        {"votePubkey": "v1", "nodePubkey": "n1", "activatedStake": 4_000_000 * 10**9, "commission": 5},
        {"votePubkey": "v2", "nodePubkey": "n2", "activatedStake": 3_000_000 * 10**9, "commission": 0},
        {"votePubkey": "v3", "nodePubkey": "n3", "activatedStake": 2_000_000 * 10**9, "commission": 10},
        {"votePubkey": "v4", "nodePubkey": "n4", "activatedStake": 1_000_000 * 10**9, "commission": 0},
    ],
    "delinquent": [
        {"votePubkey": "v5", "nodePubkey": "n5", "activatedStake": 100_000 * 10**9, "commission": 7},
    ],
}

SUPPLY = {
    "context": {"slot": 440580196},
    "value": {"circulating": 583_000_000 * 10**9, "nonCirculating": 49_000_000 * 10**9, "total": 632_000_000 * 10**9},
}

INFLATION_RATE = {"epoch": 1019, "foundation": 0.0, "total": 0.036884, "validator": 0.036884}

ALL_RPC = {
    "getHealth": "ok",
    "getEpochInfo": EPOCH_INFO,
    "getRecentPerformanceSamples": PERF_SAMPLES,
    "getVoteAccounts": VOTE_ACCOUNTS,
    "getSupply": SUPPLY,
    "getInflationRate": INFLATION_RATE,
}
