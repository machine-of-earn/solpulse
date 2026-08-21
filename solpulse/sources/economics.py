"""Economic indicators: SOL supply and staking ratio (on-chain, keyless)."""

from __future__ import annotations

from ..rpc import RpcClient

NAME = "economics"

LAMPORTS_PER_SOL = 1_000_000_000


def _sol(lamports: float) -> float:
    return round(lamports / LAMPORTS_PER_SOL, 3)


def collect(client: RpcClient) -> dict:
    """Collect SOL supply figures and inflation.

    `excludeNonCirculatingAccountsList` keeps the response small — the full
    list is thousands of pubkeys we never use.
    """
    supply, inflation = client.batch(
        [
            ("getSupply", [{"excludeNonCirculatingAccountsList": True}]),
            ("getInflationRate", None),
        ]
    )

    value = supply.get("value") or {}
    circulating = float(value.get("circulating", 0))
    non_circulating = float(value.get("nonCirculating", 0))
    total = float(value.get("total", 0))

    return {
        "total_supply_sol": _sol(total),
        "circulating_supply_sol": _sol(circulating),
        "non_circulating_supply_sol": _sol(non_circulating),
        "circulating_pct": round(100.0 * circulating / total, 2) if total else None,
        "inflation_total_pct": round(100.0 * float(inflation.get("total", 0)), 3),
        "inflation_validator_pct": round(100.0 * float(inflation.get("validator", 0)), 3),
        "inflation_epoch": inflation.get("epoch"),
    }
