"""Network performance: epoch progress, block height, throughput, health."""

from __future__ import annotations

from ..rpc import RpcClient

NAME = "network"

# Solana targets 400ms slots; used only to label the observed rate, never to
# substitute for it.
TARGET_SLOT_MS = 400.0


def _throughput(samples: list[dict]) -> dict:
    """Reduce getRecentPerformanceSamples to TPS figures.

    Vote transactions dominate raw TPS on Solana, so we report both the raw
    number and the non-vote ("real user activity") number, which is what
    ecosystem reports usually mean by TPS.
    """
    usable = [
        s
        for s in samples
        if isinstance(s, dict) and s.get("samplePeriodSecs")
    ]
    if not usable:
        raise ValueError("getRecentPerformanceSamples returned no usable samples")

    latest = usable[0]
    period = float(latest["samplePeriodSecs"])
    total_tx = sum(float(s.get("numTransactions", 0)) for s in usable)
    total_nonvote = sum(float(s.get("numNonVoteTransactions", 0)) for s in usable)
    total_slots = sum(float(s.get("numSlots", 0)) for s in usable)
    total_period = sum(float(s["samplePeriodSecs"]) for s in usable)

    return {
        "tps_latest": round(float(latest.get("numTransactions", 0)) / period, 2),
        "tps_nonvote_latest": round(float(latest.get("numNonVoteTransactions", 0)) / period, 2),
        "tps_mean": round(total_tx / total_period, 2),
        "tps_nonvote_mean": round(total_nonvote / total_period, 2),
        "slot_time_ms_mean": round(total_period * 1000.0 / total_slots, 1) if total_slots else None,
        "target_slot_time_ms": TARGET_SLOT_MS,
        "samples_used": len(usable),
        "sample_window_secs": int(total_period),
    }


def collect(client: RpcClient, sample_count: int = 30) -> dict:
    """Collect network performance metrics in a single batched round-trip."""
    health, epoch, samples = client.batch(
        [
            ("getHealth", None),
            ("getEpochInfo", None),
            ("getRecentPerformanceSamples", [sample_count]),
        ]
    )

    slots_in_epoch = epoch.get("slotsInEpoch") or 0
    slot_index = epoch.get("slotIndex") or 0
    progress = round(100.0 * slot_index / slots_in_epoch, 2) if slots_in_epoch else None

    throughput = _throughput(samples if isinstance(samples, list) else [])
    slot_ms = throughput.get("slot_time_ms_mean")
    remaining = slots_in_epoch - slot_index
    eta_secs = int(remaining * slot_ms / 1000.0) if slot_ms and remaining > 0 else None

    return {
        "health": health,
        "epoch": epoch.get("epoch"),
        "absolute_slot": epoch.get("absoluteSlot"),
        "block_height": epoch.get("blockHeight"),
        "transaction_count": epoch.get("transactionCount"),
        "epoch_progress_pct": progress,
        "epoch_slots_remaining": remaining if slots_in_epoch else None,
        "epoch_eta_seconds": eta_secs,
        "throughput": throughput,
    }
