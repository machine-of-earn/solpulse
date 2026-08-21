"""Render a snapshot as a human-readable Markdown report.

The listing asks for three output formats over one collection run; this is the
"human-readable" one. It is deliberately plain CommonMark — no HTML fallbacks,
no emoji-as-data — so it reads correctly in a terminal, on GitHub, and in a
plain text editor.
"""

from __future__ import annotations

from ..snapshot import get
from .format import (
    ARROWS,
    UNKNOWN,
    compact,
    direction,
    duration,
    num,
    pct,
    shorten,
    signed_pct,
    usd,
    usd_compact,
)

TOP_VALIDATOR_ROWS = 10


def _delta(value) -> str:
    """"▲ +3.74%" — arrow and sign, so the direction never rests on color."""
    if value is None:
        return UNKNOWN
    return f"{ARROWS[direction(value)]} {signed_pct(value)}"


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(row) + " |" for row in rows]
    return out


def _health_line(snap: dict) -> str:
    health = get(snap, "network", "health")
    if health is None:
        return f"- **Network health:** {UNKNOWN} (source unavailable)"
    if health == "ok":
        return "- **Network health:** OK — RPC node reports a healthy, caught-up cluster."
    return f"- **Network health:** DEGRADED — node reports `{health}`."


def _headline(snap: dict) -> list[str]:
    """The three or four sentences a reader gets if they read nothing else."""
    lines = ["## At a glance", ""]
    lines.append(_health_line(snap))

    epoch = get(snap, "network", "epoch")
    progress = get(snap, "network", "epoch_progress_pct")
    eta = get(snap, "network", "epoch_eta_seconds")
    lines.append(
        f"- **Epoch {num(epoch)}** is {pct(progress)} complete "
        f"(~{duration(eta)} remaining)."
    )

    tps = get(snap, "network", "throughput", "tps_mean")
    tps_nv = get(snap, "network", "throughput", "tps_nonvote_mean")
    slot_ms = get(snap, "network", "throughput", "slot_time_ms_mean")
    lines.append(
        f"- **Throughput:** {num(tps, 0)} TPS total, {num(tps_nv, 0)} TPS excluding "
        f"vote transactions, at a measured {num(slot_ms, 1)} ms slot time."
    )

    active = get(snap, "validators", "active_count")
    delinquent = get(snap, "validators", "delinquent_count")
    lines.append(
        f"- **Validators:** {num(active)} active, {num(delinquent)} delinquent "
        f"({pct(get(snap, 'validators', 'delinquent_stake_pct'), 3)} of stake)."
    )

    price = get(snap, "price", "sol_usd")
    tvl = get(snap, "tvl", "tvl_usd")
    lines.append(
        f"- **Market:** SOL {usd(price)} ({_delta(get(snap, 'price', 'sol_usd_24h_change_pct'))} 24h); "
        f"DeFi TVL {usd_compact(tvl)} ({_delta(get(snap, 'tvl', 'tvl_change_7d_pct'))} 7d)."
    )
    return lines


def _network(snap: dict) -> list[str]:
    rows = [
        ["Health", str(get(snap, "network", "health") or UNKNOWN)],
        ["Epoch", num(get(snap, "network", "epoch"))],
        ["Epoch progress", pct(get(snap, "network", "epoch_progress_pct"))],
        ["Slots remaining", num(get(snap, "network", "epoch_slots_remaining"))],
        ["Epoch ETA", duration(get(snap, "network", "epoch_eta_seconds"))],
        ["Absolute slot", num(get(snap, "network", "absolute_slot"))],
        ["Block height", num(get(snap, "network", "block_height"))],
        ["Transaction count", num(get(snap, "network", "transaction_count"))],
        ["TPS (mean, all)", num(get(snap, "network", "throughput", "tps_mean"), 2)],
        ["TPS (mean, non-vote)", num(get(snap, "network", "throughput", "tps_nonvote_mean"), 2)],
        ["TPS (latest sample)", num(get(snap, "network", "throughput", "tps_latest"), 2)],
        ["Slot time (measured)", num(get(snap, "network", "throughput", "slot_time_ms_mean"), 1, " ms")],
        ["Slot time (target)", num(get(snap, "network", "throughput", "target_slot_time_ms"), 1, " ms")],
        ["Sample window", duration(get(snap, "network", "throughput", "sample_window_secs"))],
    ]
    return ["## Network performance", ""] + _table(["Metric", "Value"], rows)


def _validators(snap: dict) -> list[str]:
    rows = [
        ["Active validators", num(get(snap, "validators", "active_count"))],
        ["Delinquent validators", num(get(snap, "validators", "delinquent_count"))],
        ["Delinquent share (count)", pct(get(snap, "validators", "delinquent_pct"))],
        ["Delinquent share (stake)", pct(get(snap, "validators", "delinquent_stake_pct"), 3)],
        ["Delinquent stake", num(get(snap, "validators", "delinquent_stake_sol"), 0, " SOL")],
        ["Superminority size", num(get(snap, "validators", "superminority_count"))],
        ["Top-10 stake share", pct(get(snap, "validators", "top10_stake_share_pct"))],
        ["Median commission", pct(get(snap, "validators", "median_commission_pct"), 0)],
    ]
    out = ["## Validators", ""] + _table(["Metric", "Value"], rows)

    top = get(snap, "validators", "top_validators", default=[]) or []
    if top:
        out += ["", f"### Top {min(len(top), TOP_VALIDATOR_ROWS)} validators by active stake", ""]
        out += _table(
            ["#", "Vote account", "Stake (SOL)", "Share", "Commission"],
            [
                [
                    str(rank),
                    f"`{shorten(v.get('vote_pubkey'))}`",
                    num(v.get("stake_sol"), 0),
                    pct(v.get("stake_share_pct"), 3),
                    pct(v.get("commission_pct"), 0),
                ]
                for rank, v in enumerate(top[:TOP_VALIDATOR_ROWS], start=1)
            ],
        )
        out += [
            "",
            "> Stake concentration is the number to watch here: the *superminority* is the "
            "smallest set of validators controlling more than one third of active stake, "
            "the threshold at which a colluding group could halt consensus.",
        ]
    return out


def _economics(snap: dict) -> list[str]:
    rows = [
        ["SOL price", usd(get(snap, "price", "sol_usd"))],
        ["SOL 24h change", _delta(get(snap, "price", "sol_usd_24h_change_pct"))],
        ["Market cap", usd_compact(get(snap, "price", "sol_market_cap_usd"))],
        ["DeFi TVL", usd_compact(get(snap, "tvl", "tvl_usd"))],
        ["TVL 1d change", _delta(get(snap, "tvl", "tvl_change_1d_pct"))],
        ["TVL 7d change", _delta(get(snap, "tvl", "tvl_change_7d_pct"))],
        ["TVL 30d change", _delta(get(snap, "tvl", "tvl_change_30d_pct"))],
        ["Circulating supply", num(get(snap, "economics", "circulating_supply_sol"), 0, " SOL")],
        ["Non-circulating supply", num(get(snap, "economics", "non_circulating_supply_sol"), 0, " SOL")],
        ["Total supply", num(get(snap, "economics", "total_supply_sol"), 0, " SOL")],
        ["Circulating share", pct(get(snap, "economics", "circulating_pct"))],
        ["Inflation (total)", pct(get(snap, "economics", "inflation_total_pct"), 3)],
        ["Inflation (validator)", pct(get(snap, "economics", "inflation_validator_pct"), 3)],
    ]
    return ["## Economics", ""] + _table(["Metric", "Value"], rows)


SEVERITY_MARK = {"critical": "‼", "warn": "!", "info": "·"}


def _anomalies(snap: dict) -> list[str]:
    """Anomaly section. Absent from pre-history snapshots, so it renders nothing."""
    report = snap.get("anomalies")
    if not isinstance(report, dict):
        return []

    found = report.get("anomalies") or []
    base = report.get("baseline") or {}
    out = ["## Anomalies", ""]

    if not found:
        out.append("No anomalies detected against the rules below.")
    else:
        rows = []
        for item in found:
            mark = SEVERITY_MARK.get(item.get("severity"), "?")
            sigma = item.get("deviation_sigma")
            rows.append([
                f"{mark} {str(item.get('severity', UNKNOWN)).upper()}",
                f"`{item.get('metric') or '—'}`",
                f"{sigma:.1f}σ" if isinstance(sigma, (int, float)) else "—",
                str(item.get("message", UNKNOWN)),
            ])
        out += _table(["Severity", "Metric", "Deviation", "What was seen"], rows)

    records = base.get("records")
    out += ["", "### How this was judged", ""]
    out.append(
        f"- **Baseline:** {num(records)} prior record(s) in the history log"
        + (f", {base.get('first')} → {base.get('last')}." if records else ".")
    )
    if base.get("sufficient"):
        checked = report.get("checked") or []
        out.append(
            f"- **Deviation rules ran** on {len(checked)} metric(s), comparing each "
            "against the *median* and *median absolute deviation* of its own recent "
            "history — robust statistics, so one past spike cannot mask the next one."
        )
    else:
        out.append(
            f"- **Deviation rules did not run:** the baseline needs at least "
            f"{num(base.get('min_required'))} records. Absolute threshold rules "
            "(node health, delinquent stake, slot time, failed sources) still applied — "
            "those need no history. No baseline was invented from too few points."
        )
    skipped = report.get("skipped") or {}
    if skipped and base.get("sufficient"):
        names = ", ".join(f"`{name}`" for name in sorted(skipped))
        out.append(f"- **Skipped:** {names} — see `skipped` in the JSON for the reason each.")
    return out


def _sources(snap: dict) -> list[str]:
    rows = []
    for name, entry in sorted((snap.get("sources") or {}).items()):
        ok = entry.get("status") == "ok"
        detail = "collected" if ok else f"`{entry.get('error', UNKNOWN)}`"
        rows.append([
            f"`{name}`",
            "ok" if ok else "ERROR",
            num(entry.get("elapsed_ms"), 1, " ms"),
            detail,
        ])
    summary = snap.get("summary") or {}
    out = ["## Data sources", ""]
    out += _table(["Source", "Status", "Latency", "Detail"], rows)
    out += [
        "",
        f"**{summary.get('sources_ok', UNKNOWN)} of {summary.get('sources_total', UNKNOWN)} "
        f"sources returned data.**",
    ]
    if summary.get("failed_sources"):
        out += [
            "",
            "> A failed source is reported as an error, never as a zero. Any metric it "
            "would have supplied reads `unknown` above.",
        ]
    return out


def render_markdown(snap: dict) -> str:
    """Return the full Markdown report for one snapshot."""
    generated = snap.get("generated_at", UNKNOWN)
    lines = [
        "# Solana ecosystem report",
        "",
        f"*Generated {generated} by {snap.get('generator', 'solpulse')} "
        f"(`{snap.get('schema', UNKNOWN)}`).*",
        "",
    ]
    for section in (_headline, _anomalies, _network, _validators, _economics, _sources):
        lines += section(snap)
        lines += [""]
    lines += [
        "---",
        "",
        "Collected directly from the Solana JSON-RPC API plus keyless public endpoints "
        "(CoinGecko, DefiLlama). No API keys, no accounts, no dependencies beyond the "
        "Python standard library.",
        "",
    ]
    return "\n".join(lines)
