"""Append-only snapshot history, and the flat metric records built from it.

Why a separate record rather than storing whole snapshots: a snapshot is a
nested document with per-source envelopes, and the questions history has to
answer ("was TPS unusual an hour ago?") are all about a handful of scalars.
Flattening at write time keeps the log small enough to keep forever, readable
with `grep`, and trivial to turn into a series.

The file is JSONL — one self-contained JSON object per line. That format is
chosen for a specific failure mode: a process killed mid-append corrupts at
most the final line, and `load()` skips unparseable lines instead of losing
the whole history.

The rule from the collector carries over unchanged: a metric that could not be
measured is **absent** from the record. It is never written as 0, so a reader
can always tell "zero" from "unknown", and a gap never drags a baseline down.
"""

from __future__ import annotations

import json
import os
from typing import Iterable

from .snapshot import get

SCHEMA = "solpulse/record/v1"

# metric key -> (source, *path). One flat namespace, stable across versions:
# adding a metric is additive, and old records simply lack the new key.
METRICS: dict[str, tuple] = {
    "epoch": ("network", "epoch"),
    "epoch_progress_pct": ("network", "epoch_progress_pct"),
    "absolute_slot": ("network", "absolute_slot"),
    "block_height": ("network", "block_height"),
    "tps_mean": ("network", "throughput", "tps_mean"),
    "tps_nonvote_mean": ("network", "throughput", "tps_nonvote_mean"),
    "slot_time_ms_mean": ("network", "throughput", "slot_time_ms_mean"),
    "active_count": ("validators", "active_count"),
    "delinquent_count": ("validators", "delinquent_count"),
    "delinquent_stake_pct": ("validators", "delinquent_stake_pct"),
    "superminority_count": ("validators", "superminority_count"),
    "top10_stake_share_pct": ("validators", "top10_stake_share_pct"),
    "sol_usd": ("price", "sol_usd"),
    "sol_usd_24h_change_pct": ("price", "sol_usd_24h_change_pct"),
    "tvl_usd": ("tvl", "tvl_usd"),
    "tvl_change_7d_pct": ("tvl", "tvl_change_7d_pct"),
    "circulating_supply_sol": ("economics", "circulating_supply_sol"),
    "total_supply_sol": ("economics", "total_supply_sol"),
    "inflation_total_pct": ("economics", "inflation_total_pct"),
}

# Metrics that are non-numeric or monotonic counters, so a "this moved a lot"
# rule would be meaningless on them. Kept in the record for context anyway.
MONOTONIC = frozenset(
    {"epoch", "epoch_progress_pct", "absolute_slot", "block_height", "total_supply_sol"}
)


def _is_num(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def record_from_snapshot(snap: dict) -> dict:
    """Flatten one snapshot into a history record.

    Only numeric, actually-measured metrics are written. `health` and the
    source tally ride along because they are what an operator asks about
    first when a record looks strange.
    """
    metrics = {}
    for key, path in METRICS.items():
        value = get(snap, path[0], *path[1:])
        if _is_num(value):
            metrics[key] = value

    summary = snap.get("summary") or {}
    return {
        "schema": SCHEMA,
        "generated_at": snap.get("generated_at"),
        "health": get(snap, "network", "health"),
        "sources_ok": summary.get("sources_ok"),
        "sources_total": summary.get("sources_total"),
        "failed_sources": list(summary.get("failed_sources") or []),
        "metrics": metrics,
    }


def append(path: str, snap: dict) -> dict:
    """Append this snapshot's record to the JSONL log at `path`, and return it.

    Opened in append mode with a trailing newline written in the same call, so
    concurrent ticks interleave whole lines rather than shredding each other.
    """
    record = record_from_snapshot(snap)
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")
    return record


def load(path: str, limit: int | None = None) -> list[dict]:
    """Read records oldest-first. A missing file is an empty history, not an error.

    Unparseable lines are skipped: a half-written final line from an
    interrupted append must not destroy the readable history in front of it.
    """
    if not os.path.exists(path):
        return []
    records = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict) and isinstance(item.get("metrics"), dict):
                records.append(item)
    if limit is not None and limit >= 0:
        records = records[-limit:] if limit else []
    return records


def series(records: Iterable[dict], metric: str) -> list[tuple]:
    """[(generated_at, value)] for one metric, skipping records that lack it."""
    out = []
    for record in records:
        value = (record.get("metrics") or {}).get(metric)
        if _is_num(value):
            out.append((record.get("generated_at"), value))
    return out


def values(records: Iterable[dict], metric: str) -> list[float]:
    return [value for _, value in series(records, metric)]
