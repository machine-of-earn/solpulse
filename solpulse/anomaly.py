"""Anomaly detection over a snapshot and its recorded history.

Two independent families of rule, deliberately kept separate:

* **Thresholds** — absolute facts that are alarming on their own and need no
  history at all ("the node reports unhealthy", "15% of stake is delinquent").
  These fire on the very first run the machine ever makes.
* **Deviations** — "this is unlike this chain's own recent behaviour", which
  needs a baseline. Computed with the **median and the median absolute
  deviation**, not mean and standard deviation: one 3,000-TPS spike in the
  window would inflate a standard deviation enough to hide the next spike,
  whereas the median barely moves. Every deviation rule also has to clear a
  minimum *relative* change, so a metric that is merely very steady does not
  emit an alert every time it twitches in the last decimal place.

Honesty rules, inherited from the collector:

* With fewer than `MIN_SAMPLES` prior records, deviation rules do not run and
  the report says so. No baseline is invented from two points.
* A metric missing from the current snapshot is skipped and named in
  `skipped`; it is never treated as a drop to zero.
* If the baseline has no spread at all, the sigma is reported as `null` rather
  than as a divide-by-zero infinity, and the finding rests on the relative
  change alone.

`detect()` is pure: no clock, no network, no filesystem.
"""

from __future__ import annotations

from .history import MONOTONIC, record_from_snapshot

SCHEMA = "solpulse/anomalies/v1"

# Below this many prior records, deviation rules are skipped entirely.
MIN_SAMPLES = 5
# Records considered for the baseline (most recent first). At a 4h tick this is
# a bit over four days of context.
WINDOW = 30

# Scales the MAD to estimate a normal distribution's sigma, so the thresholds
# below can be read in familiar "sigma" terms.
MAD_TO_SIGMA = 1.4826

FLAG_SIGMA = 3.5
CRITICAL_SIGMA = 6.0

SEVERITY_RANK = {"critical": 0, "warn": 1, "info": 2}

# metric -> (label, minimum relative change to bother reporting, unit)
DEVIATION_RULES: dict[str, tuple] = {
    "tps_nonvote_mean": ("non-vote throughput", 0.25, "TPS"),
    "tps_mean": ("total throughput", 0.25, "TPS"),
    "slot_time_ms_mean": ("slot time", 0.15, "ms"),
    "active_count": ("active validator count", 0.05, "validators"),
    "delinquent_count": ("delinquent validator count", 0.50, "validators"),
    "delinquent_stake_pct": ("delinquent stake share", 0.50, "%"),
    "top10_stake_share_pct": ("top-10 stake share", 0.10, "%"),
    "sol_usd": ("SOL price", 0.05, "USD"),
    "tvl_usd": ("DeFi TVL", 0.05, "USD"),
    "circulating_supply_sol": ("circulating supply", 0.01, "SOL"),
}


def _is_num(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def median(values: list[float]):
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def mad(values: list[float]):
    """Median absolute deviation — spread that a single outlier cannot inflate."""
    center = median(values)
    if center is None:
        return None
    return median([abs(value - center) for value in values])


def robust_sigma(values: list[float]):
    """MAD rescaled to sigma units, or None when the baseline has no spread."""
    spread = mad(values)
    if spread is None or spread == 0:
        return None
    return spread * MAD_TO_SIGMA


def _finding(code, severity, message, **extra) -> dict:
    finding = {"code": code, "severity": severity, "message": message}
    finding.update(extra)
    return finding


def _threshold_findings(record: dict) -> list[dict]:
    """Rules that need no history. These fire on the machine's first ever run."""
    out = []
    metrics = record.get("metrics") or {}
    health = record.get("health")

    if health is not None and health != "ok":
        out.append(_finding(
            "network_unhealthy", "critical",
            f"RPC node reports health {health!r}, not 'ok'.",
            metric="health", value=health,
        ))

    failed = record.get("failed_sources") or []
    if failed:
        out.append(_finding(
            "source_failure", "warn",
            f"{len(failed)} data source(s) failed this run: {', '.join(sorted(failed))}. "
            "Metrics from those sources are unknown, not zero.",
            metric=None, value=sorted(failed),
        ))

    stake = metrics.get("delinquent_stake_pct")
    if _is_num(stake):
        if stake >= 15.0:
            out.append(_finding(
                "delinquent_stake_high", "critical",
                f"{stake:.3f}% of stake is delinquent — approaching the 33.3% "
                "share at which the cluster stops finalising blocks.",
                metric="delinquent_stake_pct", value=stake, threshold=15.0,
            ))
        elif stake >= 5.0:
            out.append(_finding(
                "delinquent_stake_elevated", "warn",
                f"{stake:.3f}% of stake is delinquent (normal is well under 1%).",
                metric="delinquent_stake_pct", value=stake, threshold=5.0,
            ))

    slot_ms = metrics.get("slot_time_ms_mean")
    if _is_num(slot_ms):
        if slot_ms >= 800:
            out.append(_finding(
                "slot_time_high", "critical",
                f"Measured slot time {slot_ms:.0f} ms is roughly double the "
                "~400 ms target — the chain is producing blocks slowly.",
                metric="slot_time_ms_mean", value=slot_ms, threshold=800,
            ))
        elif slot_ms >= 600:
            out.append(_finding(
                "slot_time_elevated", "warn",
                f"Measured slot time {slot_ms:.0f} ms is above the ~400 ms target.",
                metric="slot_time_ms_mean", value=slot_ms, threshold=600,
            ))

    supermin = metrics.get("superminority_count")
    if _is_num(supermin) and supermin <= 20:
        out.append(_finding(
            "superminority_concentrated", "info",
            f"Only {supermin:.0f} validators hold the superminority — that many "
            "colluding or failing together could halt finality.",
            metric="superminority_count", value=supermin, threshold=20,
        ))

    return out


def _deviation_findings(record: dict, history: list[dict]) -> tuple[list[dict], list[str], dict]:
    out: list[dict] = []
    checked: list[str] = []
    skipped: dict[str, str] = {}
    metrics = record.get("metrics") or {}

    for metric, (label, min_rel, unit) in DEVIATION_RULES.items():
        if metric in MONOTONIC:  # guard: a counter has no meaningful baseline
            skipped[metric] = "monotonic metric, deviation is not meaningful"
            continue
        value = metrics.get(metric)
        if not _is_num(value):
            skipped[metric] = "not measured in this snapshot"
            continue

        baseline = [
            record_metrics[metric]
            for record_metrics in ((item.get("metrics") or {}) for item in history)
            if _is_num(record_metrics.get(metric))
        ]
        if len(baseline) < MIN_SAMPLES:
            skipped[metric] = f"only {len(baseline)} baseline point(s), need {MIN_SAMPLES}"
            continue

        checked.append(metric)
        center = median(baseline)
        sigma = robust_sigma(baseline)
        delta = value - center
        # A relative gate keeps very steady metrics from alerting on noise.
        relative = abs(delta) / abs(center) if center else (0.0 if delta == 0 else 1.0)
        if relative < min_rel:
            continue

        if sigma is None:
            # Flat baseline: no spread to measure against, so the relative move
            # is the whole finding. Reported with a null sigma, never infinity.
            severity = "warn"
            deviation = None
            basis = (
                f"its {len(baseline)} previous readings were all {center:,.2f}, "
                "so there is no spread to compare against"
            )
        else:
            deviation = abs(delta) / sigma
            if deviation < FLAG_SIGMA:
                continue
            severity = "critical" if deviation >= CRITICAL_SIGMA else "warn"
            basis = (
                f"{deviation:.1f}x the typical spread of its last "
                f"{len(baseline)} readings (median {center:,.2f})"
            )

        way = "above" if delta > 0 else "below"
        out.append(_finding(
            "deviation", severity,
            f"{label} is {value:,.2f} {unit}, {relative * 100:.1f}% {way} normal — {basis}.",
            metric=metric,
            value=value,
            baseline_median=center,
            delta=delta,
            relative_change_pct=relative * 100,
            deviation_sigma=deviation,
            direction="up" if delta > 0 else "down",
            samples=len(baseline),
        ))

    return out, checked, skipped


def detect(snap: dict, history: list[dict] | None = None, *, window: int = WINDOW) -> dict:
    """Compare `snap` against `history` (older records, current one excluded).

    Returns the report envelope that gets embedded in the snapshot under
    `"anomalies"` and rendered by every output format.
    """
    history = list(history or [])
    if window and window > 0:
        history = history[-window:]
    record = record_from_snapshot(snap)

    findings = _threshold_findings(record)
    sufficient = len(history) >= MIN_SAMPLES
    if sufficient:
        deviations, checked, skipped = _deviation_findings(record, history)
        findings += deviations
    else:
        checked, skipped = [], {
            metric: f"history has {len(history)} record(s), need {MIN_SAMPLES}"
            for metric in DEVIATION_RULES
        }

    findings.sort(key=lambda item: (SEVERITY_RANK.get(item["severity"], 9), item.get("metric") or ""))
    counts = {level: 0 for level in SEVERITY_RANK}
    for item in findings:
        counts[item["severity"]] = counts.get(item["severity"], 0) + 1

    return {
        "schema": SCHEMA,
        "anomalies": findings,
        "counts": counts,
        "worst": findings[0]["severity"] if findings else None,
        "baseline": {
            "records": len(history),
            "min_required": MIN_SAMPLES,
            "window": window,
            "sufficient": sufficient,
            "first": history[0].get("generated_at") if history else None,
            "last": history[-1].get("generated_at") if history else None,
        },
        "checked": sorted(checked),
        "skipped": skipped,
    }
