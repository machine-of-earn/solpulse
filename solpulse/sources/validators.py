"""Validator set: active vs delinquent, stake concentration, commission."""

from __future__ import annotations

from ..rpc import RpcClient

NAME = "validators"

LAMPORTS_PER_SOL = 1_000_000_000

# A superminority is the smallest set of validators that together control more
# than 1/3 of stake — the number of nodes that could halt the chain by going
# offline. Lower is worse for decentralisation.
SUPERMINORITY_THRESHOLD = 1.0 / 3.0


def _sol(lamports: float) -> float:
    return round(lamports / LAMPORTS_PER_SOL, 3)


def _summarise(accounts: list[dict], top_n: int) -> dict:
    stakes = sorted((float(a.get("activatedStake", 0)) for a in accounts), reverse=True)
    total = sum(stakes)

    superminority = 0
    running = 0.0
    if total:
        for stake in stakes:
            running += stake
            superminority += 1
            if running / total > SUPERMINORITY_THRESHOLD:
                break

    ranked = sorted(accounts, key=lambda a: float(a.get("activatedStake", 0)), reverse=True)
    top = [
        {
            "vote_pubkey": a.get("votePubkey"),
            "node_pubkey": a.get("nodePubkey"),
            "stake_sol": _sol(float(a.get("activatedStake", 0))),
            "stake_share_pct": round(100.0 * float(a.get("activatedStake", 0)) / total, 3) if total else None,
            "commission_pct": a.get("commission"),
        }
        for a in ranked[:top_n]
    ]

    return {
        "total_stake_sol": _sol(total),
        "top_validators": top,
        "top10_stake_share_pct": round(100.0 * sum(stakes[:10]) / total, 2) if total else None,
        "superminority_count": superminority or None,
    }


def collect(client: RpcClient, top_n: int = 10) -> dict:
    """Collect validator-set health and stake distribution."""
    vote_accounts = client.call("getVoteAccounts")

    current = vote_accounts.get("current") or []
    delinquent = vote_accounts.get("delinquent") or []
    active_count = len(current)
    delinquent_count = len(delinquent)
    total_count = active_count + delinquent_count

    summary = _summarise(current, top_n)

    delinquent_stake = sum(float(a.get("activatedStake", 0)) for a in delinquent)
    all_stake = float(summary["total_stake_sol"]) * LAMPORTS_PER_SOL + delinquent_stake

    commissions = sorted(
        a.get("commission") for a in current if isinstance(a.get("commission"), int)
    )
    median_commission = commissions[len(commissions) // 2] if commissions else None
    zero_commission = sum(1 for c in commissions if c == 0)

    return {
        "active_count": active_count,
        "delinquent_count": delinquent_count,
        "total_count": total_count,
        "delinquent_pct": round(100.0 * delinquent_count / total_count, 2) if total_count else None,
        "delinquent_stake_sol": _sol(delinquent_stake),
        "delinquent_stake_pct": round(100.0 * delinquent_stake / all_stake, 3) if all_stake else None,
        "median_commission_pct": median_commission,
        "zero_commission_count": zero_commission,
        **summary,
    }
