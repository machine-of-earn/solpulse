"""Off-chain context: SOL price and Solana DeFi TVL.

Both endpoints are public and keyless. They are separate sources so that a
rate-limited price lookup cannot take the TVL figure — or the whole report —
down with it.
"""

from __future__ import annotations

from ..http import get_json

COINGECKO_URL = (
    "https://api.coingecko.com/api/v3/simple/price"
    "?ids=solana&vs_currencies=usd&include_24hr_change=true&include_market_cap=true"
)
DEFILLAMA_URL = "https://api.llama.fi/v2/historicalChainTvl/Solana"


def collect_price(timeout: float = 20.0) -> dict:
    """SOL spot price and 24h change from CoinGecko's keyless endpoint."""
    data = get_json(COINGECKO_URL, timeout=timeout)
    sol = (data or {}).get("solana") or {}
    if "usd" not in sol:
        raise ValueError(f"CoinGecko response missing solana.usd: {data!r}")
    return {
        "sol_usd": sol["usd"],
        "sol_usd_24h_change_pct": round(float(sol["usd_24h_change"]), 2)
        if sol.get("usd_24h_change") is not None
        else None,
        "sol_market_cap_usd": sol.get("usd_market_cap"),
        "source": "coingecko",
    }


def collect_tvl(timeout: float = 20.0) -> dict:
    """Solana DeFi TVL now, plus 1d/7d/30d deltas from DefiLlama's history."""
    series = get_json(DEFILLAMA_URL, timeout=timeout)
    if not isinstance(series, list) or not series:
        raise ValueError("DefiLlama returned an empty TVL series")

    points = [p for p in series if isinstance(p, dict) and "tvl" in p]
    if not points:
        raise ValueError("DefiLlama series contained no tvl points")

    latest = points[-1]
    current = float(latest["tvl"])

    def change(days: int) -> float | None:
        if len(points) <= days:
            return None
        past = float(points[-1 - days]["tvl"])
        return round(100.0 * (current - past) / past, 2) if past else None

    return {
        "tvl_usd": round(current, 2),
        "tvl_as_of": latest.get("date"),
        "tvl_change_1d_pct": change(1),
        "tvl_change_7d_pct": change(7),
        "tvl_change_30d_pct": change(30),
        "history_days": len(points),
        "source": "defillama",
    }
