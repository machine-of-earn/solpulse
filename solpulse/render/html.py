"""Render a snapshot as a self-contained, interactive, dark-theme HTML dashboard.

Design constraints, in priority order:

1. **Self-contained.** One file, no CDN, no fonts, no build step, no JS
   framework. It opens from `file://` and hosts as a static asset. This matches
   the same "no external dependencies" rule the collector follows.
2. **Dark by default, light available.** The theme toggle sets `data-theme` on
   the root and is honoured over the OS setting in both directions.
3. **Form follows the data's job.** Single headline numbers are stat tiles, not
   one-bar charts; epoch progress is a meter (one ratio against a limit); stake
   by validator is a magnitude comparison, so it is a one-hue sequential bar
   chart, not eight cycled colors; supply is part-to-whole, so it is a stacked
   bar.
4. **Never color alone.** Deltas carry an arrow and a signed number, statuses
   carry a glyph and a word, and every chart has a table view.

Colors are the reference palette's documented dark steps, used unmodified.
"""

from __future__ import annotations

import html
import json

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

# Sequential blue ramp, lightest = largest, so magnitude reads as "brighter" on
# the dark surface. Bounded by the documented ordinal floor for dark (step 600).
STAKE_RAMP = [
    "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5",
    "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#184f95",
]


def esc(value) -> str:
    """HTML-escape any value. Chain data is untrusted input, not trusted text."""
    if value is None:
        return UNKNOWN
    return html.escape(str(value), quote=True)


def embed_json(data) -> str:
    """Serialise for a <script type="application/json"> block.

    Escaping `<`, `>` and `&` is what stops a string in the data ending the
    script element early; U+2028/2029 are escaped because they are raw line
    terminators in JS source.
    """
    text = json.dumps(data, indent=2, sort_keys=True)
    return (
        text.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace(" ", "\\u2028")
        .replace(" ", "\\u2029")
    )


def _tile(label: str, value: str, *, delta=None, delta_label: str = "", note: str = "") -> str:
    """A stat tile: the form for a single current value, optionally with change."""
    parts = [
        '<div class="tile">',
        f'<div class="tile-label">{esc(label)}</div>',
        f'<div class="tile-value">{esc(value)}</div>',
    ]
    if delta is not None:
        way = direction(delta)
        parts.append(
            f'<div class="delta delta-{way}">'
            f'<span class="arrow" aria-hidden="true">{ARROWS[way]}</span> '
            f'<span class="delta-num">{esc(signed_pct(delta))}</span> '
            f'<span class="delta-label">{esc(delta_label)}</span></div>'
        )
    elif note:
        parts.append(f'<div class="tile-note">{esc(note)}</div>')
    parts.append("</div>")
    return "".join(parts)


def _kpi_row(snap: dict) -> str:
    tiles = [
        _tile(
            "TPS — non-vote",
            num(get(snap, "network", "throughput", "tps_nonvote_mean"), 0),
            note=f"{num(get(snap, 'network', 'throughput', 'tps_mean'), 0)} including votes",
        ),
        _tile(
            "Slot time",
            num(get(snap, "network", "throughput", "slot_time_ms_mean"), 1, " ms"),
            note=f"measured vs {num(get(snap, 'network', 'throughput', 'target_slot_time_ms'), 0, ' ms')} target",
        ),
        _tile(
            "Active validators",
            num(get(snap, "validators", "active_count")),
            note=f"{num(get(snap, 'validators', 'delinquent_count'))} delinquent",
        ),
        _tile(
            "SOL price",
            usd(get(snap, "price", "sol_usd")),
            delta=get(snap, "price", "sol_usd_24h_change_pct"),
            delta_label="24h",
        ),
        _tile(
            "DeFi TVL",
            usd_compact(get(snap, "tvl", "tvl_usd")),
            delta=get(snap, "tvl", "tvl_change_7d_pct"),
            delta_label="7d",
        ),
        _tile(
            "Block height",
            compact(get(snap, "network", "block_height"), 2),
            note=f"{num(get(snap, 'network', 'block_height'))} exactly",
        ),
    ]
    return f'<section class="kpis" aria-label="Headline metrics">{"".join(tiles)}</section>'


def _health_banner(snap: dict) -> str:
    """Status is a reserved role: it ships as glyph + word, never as color alone."""
    health = get(snap, "network", "health")
    delinquent = get(snap, "validators", "delinquent_count")
    failed = (snap.get("summary") or {}).get("failed_sources") or []

    if health is None:
        state, glyph, word = "warning", "!", "Unknown"
        detail = "The network source did not return, so cluster health is unknown."
    elif health != "ok":
        state, glyph, word = "critical", "×", "Degraded"
        detail = f"RPC node reports health {esc(health)}."
    else:
        state, glyph, word = "good", "✓", "Healthy"
        detail = "RPC node reports a healthy, caught-up cluster."
        if isinstance(delinquent, int) and delinquent > 0:
            detail += (
                f" {num(delinquent)} validators are delinquent, holding "
                f"{pct(get(snap, 'validators', 'delinquent_stake_pct'), 3)} of stake."
            )
    if failed:
        detail += f" {len(failed)} data source(s) failed — affected metrics read “unknown”."
    return (
        f'<div class="banner banner-{state}" role="status">'
        f'<span class="banner-glyph" aria-hidden="true">{glyph}</span>'
        f'<span class="banner-word">{word}</span>'
        f'<span class="banner-detail">{detail}</span></div>'
    )


# Severity is a status role: glyph + word + text, so it never rests on hue alone.
SEVERITY_STYLE = {
    "critical": ("critical", "‼", "Critical"),
    "warn": ("warning", "!", "Warning"),
    "info": ("info", "·", "Info"),
}


def _anomaly_card(snap: dict) -> str:
    """Anomalies and, just as important, how they were judged.

    Renders nothing at all for a snapshot collected without a history log —
    an empty card would imply "checked, all clear", which would be a lie.
    """
    report = snap.get("anomalies")
    if not isinstance(report, dict):
        return ""

    found = report.get("anomalies") or []
    base = report.get("baseline") or {}
    counts = report.get("counts") or {}

    pills = "".join(
        f'<span class="pill pill-{SEVERITY_STYLE[level][0]}">'
        f'<span aria-hidden="true">{SEVERITY_STYLE[level][1]}</span> '
        f'{counts.get(level, 0)} {esc(SEVERITY_STYLE[level][2].lower())}</span>'
        for level in ("critical", "warn", "info")
        if counts.get(level)
    ) or '<span class="pill pill-good"><span aria-hidden="true">✓</span> none</span>'

    if found:
        rows = []
        for item in found:
            state, glyph, word = SEVERITY_STYLE.get(
                item.get("severity"), ("info", "?", UNKNOWN)
            )
            sigma = item.get("deviation_sigma")
            shown = f"{sigma:.1f}σ" if isinstance(sigma, (int, float)) else "—"
            rows.append(
                f'<tr class="row-{state}">'
                f'<td class="lead"><span class="sev sev-{state}">'
                f'<span aria-hidden="true">{glyph}</span> {esc(word)}</span></td>'
                f'<td class="lead mono">{esc(item.get("metric") or "—")}</td>'
                f'<td>{esc(shown)}</td>'
                f'<td class="lead">{esc(item.get("message", UNKNOWN))}</td></tr>'
            )
        body = (
            '<table><thead><tr><th class="lead">Severity</th>'
            '<th class="lead">Metric</th><th>Deviation</th>'
            '<th class="lead">What was seen</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table>'
        )
    else:
        body = '<p class="empty">No anomalies detected against the rules below.</p>'

    records = base.get("records")
    if base.get("sufficient"):
        method = (
            f"Deviation rules ran on {len(report.get('checked') or [])} metric(s), each "
            "compared against the median and median absolute deviation of its own recent "
            "history — robust statistics, so one earlier spike cannot mask the next one."
        )
    else:
        method = (
            f"Deviation rules did not run: the baseline needs at least "
            f"{esc(num(base.get('min_required')))} records and has "
            f"{esc(num(records))}. Absolute threshold rules — node health, delinquent "
            "stake, slot time, failed sources — still applied; those need no history. "
            "No baseline was invented from too few points."
        )

    span = (
        f" ({esc(base.get('first'))} → {esc(base.get('last'))})"
        if records else ""
    )
    return f"""
<section class="card" aria-label="Anomaly detection">
  <div class="card-head">
    <h2>Anomalies</h2>
    <div class="pills">{pills}</div>
  </div>
  {body}
  <p class="note"><strong>Baseline:</strong> {esc(num(records))} prior record(s){span}.
     {method}</p>
</section>"""


def _epoch_meter(snap: dict) -> str:
    """A single ratio against a limit -> a meter, not a two-slice pie."""
    progress = get(snap, "network", "epoch_progress_pct")
    epoch = get(snap, "network", "epoch")
    eta = get(snap, "network", "epoch_eta_seconds")
    remaining = get(snap, "network", "epoch_slots_remaining")
    width = progress if isinstance(progress, (int, float)) else 0
    width = max(0.0, min(100.0, float(width)))
    known = isinstance(progress, (int, float))
    return f"""
<section class="card" aria-label="Epoch progress">
  <h2>Epoch {esc(num(epoch))}</h2>
  <p class="hero">{esc(pct(progress))}</p>
  <div class="meter" role="img"
       aria-label="Epoch {esc(num(epoch))} is {esc(pct(progress))} complete">
    <div class="meter-fill{'' if known else ' meter-unknown'}" style="width:{width:.2f}%"></div>
  </div>
  <dl class="pairs">
    <dt>Slots remaining</dt><dd>{esc(num(remaining))}</dd>
    <dt>Estimated time left</dt><dd>{esc(duration(eta))}</dd>
    <dt>Absolute slot</dt><dd>{esc(num(get(snap, "network", "absolute_slot")))}</dd>
    <dt>Transactions (all time)</dt><dd>{esc(num(get(snap, "network", "transaction_count")))}</dd>
  </dl>
</section>"""


def _stake_chart(snap: dict) -> str:
    """Magnitude comparison across named items -> one-hue sequential bars."""
    top = (get(snap, "validators", "top_validators", default=[]) or [])[:TOP_VALIDATOR_ROWS]
    if not top:
        return (
            '<section class="card" aria-label="Validator stake distribution">'
            "<h2>Stake by validator</h2>"
            f'<p class="empty">{UNKNOWN} — the validators source did not return.</p></section>'
        )

    shares = [v.get("stake_share_pct") for v in top]
    widest = max((s for s in shares if isinstance(s, (int, float))), default=0) or 1

    bars, rows = [], []
    for rank, v in enumerate(top):
        share = v.get("stake_share_pct")
        width = (share / widest * 100) if isinstance(share, (int, float)) else 0
        color = STAKE_RAMP[min(rank, len(STAKE_RAMP) - 1)]
        label = shorten(v.get("vote_pubkey"))
        tip = (
            f"{esc(v.get('vote_pubkey'))} — {esc(num(v.get('stake_sol'), 0))} SOL, "
            f"{esc(pct(share, 3))} of stake, {esc(pct(v.get('commission_pct'), 0))} commission"
        )
        bars.append(
            f'<div class="bar-row" tabindex="0" data-tip="{tip}">'
            f'<span class="bar-name" title="{esc(v.get("vote_pubkey"))}">{esc(label)}</span>'
            f'<span class="bar-track">'
            f'<span class="bar-fill" style="width:{width:.2f}%;background:{color}"></span></span>'
            f'<span class="bar-value">{esc(pct(share, 2))}</span></div>'
        )
        rows.append(
            f"<tr><td>{rank + 1}</td><td class=\"mono\">{esc(v.get('vote_pubkey'))}</td>"
            f"<td>{esc(num(v.get('stake_sol'), 0))}</td><td>{esc(pct(share, 3))}</td>"
            f"<td>{esc(pct(v.get('commission_pct'), 0))}</td></tr>"
        )

    return f"""
<section class="card" aria-label="Validator stake distribution">
  <div class="card-head">
    <h2>Stake by validator <span class="sub">top {len(top)} of {esc(num(get(snap, "validators", "active_count")))} active</span></h2>
    <button class="toggle" data-target="stake-table" aria-expanded="false">Table view</button>
  </div>
  <div class="bars">{"".join(bars)}</div>
  <p class="footnote">
    Top-10 share {esc(pct(get(snap, "validators", "top10_stake_share_pct")))} ·
    superminority {esc(num(get(snap, "validators", "superminority_count")))} validators —
    the smallest group controlling over one third of stake, and so the group that could halt consensus.
  </p>
  <div class="table-wrap" id="stake-table" hidden>
    <table>
      <caption>Top {len(top)} validators by active stake</caption>
      <thead><tr><th>#</th><th>Vote account</th><th>Stake (SOL)</th><th>Share</th><th>Commission</th></tr></thead>
      <tbody>{"".join(rows)}</tbody>
    </table>
  </div>
</section>"""


def _supply_chart(snap: dict) -> str:
    """Part-to-whole -> a stacked bar, with a 2px surface gap between segments."""
    circ = get(snap, "economics", "circulating_supply_sol")
    non = get(snap, "economics", "non_circulating_supply_sol")
    circ_pct = get(snap, "economics", "circulating_pct")

    if not isinstance(circ_pct, (int, float)):
        segments = f'<p class="empty">{UNKNOWN} — the economics source did not return.</p>'
    else:
        rest = max(0.0, 100.0 - float(circ_pct))
        segments = (
            '<div class="stack" role="img" aria-label='
            f'"Circulating {esc(pct(circ_pct))}, non-circulating {esc(pct(rest))}">'
            f'<span class="seg seg-1" style="width:{float(circ_pct):.2f}%"'
            f' data-tip="Circulating — {esc(num(circ, 0))} SOL ({esc(pct(circ_pct))})"></span>'
            f'<span class="seg seg-2" style="width:{rest:.2f}%"'
            f' data-tip="Non-circulating — {esc(num(non, 0))} SOL ({esc(pct(rest))})"></span>'
            "</div>"
            '<div class="legend">'
            '<span class="key"><i class="swatch sw-1"></i>Circulating '
            f'<b>{esc(pct(circ_pct))}</b></span>'
            '<span class="key"><i class="swatch sw-2"></i>Non-circulating '
            f'<b>{esc(pct(rest))}</b></span></div>'
        )

    return f"""
<section class="card" aria-label="Supply and economics">
  <h2>Supply &amp; economics</h2>
  {segments}
  <dl class="pairs">
    <dt>Circulating</dt><dd>{esc(num(circ, 0, " SOL"))}</dd>
    <dt>Non-circulating</dt><dd>{esc(num(non, 0, " SOL"))}</dd>
    <dt>Total supply</dt><dd>{esc(num(get(snap, "economics", "total_supply_sol"), 0, " SOL"))}</dd>
    <dt>Inflation (total)</dt><dd>{esc(pct(get(snap, "economics", "inflation_total_pct"), 3))}</dd>
    <dt>Market cap</dt><dd>{esc(usd_compact(get(snap, "price", "sol_market_cap_usd")))}</dd>
    <dt>TVL 30d change</dt><dd>{esc(signed_pct(get(snap, "tvl", "tvl_change_30d_pct")))}</dd>
  </dl>
</section>"""


def _sources_card(snap: dict) -> str:
    rows = []
    for name, entry in sorted((snap.get("sources") or {}).items()):
        ok = entry.get("status") == "ok"
        glyph, word, state = ("✓", "ok", "good") if ok else ("×", "error", "critical")
        detail = "collected" if ok else esc(entry.get("error", UNKNOWN))
        rows.append(
            f'<li class="src src-{state}">'
            f'<span class="src-glyph" aria-hidden="true">{glyph}</span>'
            f'<span class="src-name mono">{esc(name)}</span>'
            f'<span class="src-word">{word}</span>'
            f'<span class="src-ms">{esc(num(entry.get("elapsed_ms"), 0, " ms"))}</span>'
            f'<span class="src-detail">{detail}</span></li>'
        )
    summary = snap.get("summary") or {}
    return f"""
<section class="card" aria-label="Data sources">
  <div class="card-head">
    <h2>Data sources <span class="sub">{esc(summary.get("sources_ok", UNKNOWN))} of
      {esc(summary.get("sources_total", UNKNOWN))} returned data</span></h2>
    <button class="toggle" data-target="raw-json" aria-expanded="false">Raw JSON</button>
  </div>
  <ul class="sources">{"".join(rows)}</ul>
  <p class="footnote">A source is recorded as <em>ok</em> or <em>error</em> and never as a
     plausible-looking zero, so “0” in this report always means a measured zero.</p>
  <div class="table-wrap" id="raw-json" hidden>
    <div class="raw-head"><button class="copy" type="button">Copy JSON</button></div>
    <pre class="raw"><code id="raw-code"></code></pre>
  </div>
</section>"""


_CSS = """
:root{color-scheme:dark;
  --surface-0:#111110;--surface-1:#1a1a19;--surface-2:#222221;--line:#333331;
  --text-primary:#ffffff;--text-secondary:#c3c2b7;--text-muted:#8b8a82;
  --series-1:#3987e5;--series-2:#d95926;
  --good:#0ca30c;--warning:#fab219;--critical:#d03b3b;}
:root[data-theme=light]{color-scheme:light;
  --surface-0:#f4f3f0;--surface-1:#fcfcfb;--surface-2:#f0efec;--line:#dedcd6;
  --text-primary:#0b0b0b;--text-secondary:#52514e;--text-muted:#6f6e69;
  --series-1:#2a78d6;--series-2:#eb6834;}
@media(prefers-color-scheme:light){:root:where(:not([data-theme=dark])){color-scheme:light;
  --surface-0:#f4f3f0;--surface-1:#fcfcfb;--surface-2:#f0efec;--line:#dedcd6;
  --text-primary:#0b0b0b;--text-secondary:#52514e;--text-muted:#6f6e69;
  --series-1:#2a78d6;--series-2:#eb6834;}}
*{box-sizing:border-box}
body{margin:0;background:var(--surface-0);color:var(--text-primary);
  font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.wrap{max-width:1080px;margin:0 auto;padding:32px 20px 64px}
header.top{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap}
h1{font-size:24px;margin:0 0 4px;letter-spacing:-.01em}
.stamp{color:var(--text-muted);font-size:13px;margin:0}
h2{font-size:15px;margin:0 0 14px;font-weight:600;letter-spacing:.01em}
h2 .sub{font-weight:400;color:var(--text-muted);font-size:13px;margin-left:6px}
button{font:inherit;font-size:13px;color:var(--text-secondary);background:var(--surface-2);
  border:1px solid var(--line);border-radius:7px;padding:5px 11px;cursor:pointer}
button:hover{color:var(--text-primary);border-color:var(--text-muted)}
button:focus-visible,[tabindex]:focus-visible{outline:2px solid var(--series-1);outline-offset:2px}
.banner{display:flex;align-items:baseline;gap:9px;flex-wrap:wrap;margin:22px 0;padding:11px 14px;
  border:1px solid var(--line);border-left-width:3px;border-radius:9px;background:var(--surface-1)}
.banner-good{border-left-color:var(--good)}
.banner-warning{border-left-color:var(--warning)}
.banner-critical{border-left-color:var(--critical)}
.banner-glyph{font-weight:700}
.banner-good .banner-glyph{color:var(--good)}
.banner-warning .banner-glyph{color:var(--warning)}
.banner-critical .banner-glyph{color:var(--critical)}
.banner-word{font-weight:600}
.banner-detail{color:var(--text-secondary);font-size:14px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(168px,1fr));gap:12px;margin-bottom:12px}
.tile{background:var(--surface-1);border:1px solid var(--line);border-radius:11px;padding:14px 15px}
.tile-label{color:var(--text-muted);font-size:12px;text-transform:uppercase;letter-spacing:.06em}
.tile-value{font-size:27px;font-weight:600;letter-spacing:-.02em;margin-top:5px;
  font-variant-numeric:tabular-nums}
.tile-note{color:var(--text-muted);font-size:12px;margin-top:3px}
.delta{font-size:12.5px;margin-top:3px;color:var(--text-secondary);font-variant-numeric:tabular-nums}
.delta .arrow{font-size:11px}
.delta-up .arrow{color:var(--good)}
.delta-down .arrow{color:var(--critical)}
.delta-label{color:var(--text-muted)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:12px;margin-top:12px}
.card{background:var(--surface-1);border:1px solid var(--line);border-radius:11px;padding:17px 18px}
.card-head{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:14px}
.card-head h2{margin:0}
.pills{display:flex;gap:7px;flex-wrap:wrap}
.pill{font-size:12px;border:1px solid var(--line);border-radius:999px;padding:3px 10px;
  background:var(--surface-2);color:var(--text-secondary);white-space:nowrap}
.pill-critical{border-color:var(--critical);color:var(--critical)}
.pill-warning{border-color:var(--warning);color:var(--warning)}
.pill-info{border-color:var(--line)}
.pill-good{border-color:var(--good);color:var(--good)}
.sev{white-space:nowrap;font-weight:600}
.sev-critical{color:var(--critical)}
.sev-warning{color:var(--warning)}
.sev-info{color:var(--text-secondary)}
tr.row-critical td{background:color-mix(in srgb,var(--critical) 9%,transparent)}
tr.row-warning td{background:color-mix(in srgb,var(--warning) 8%,transparent)}
.lead{text-align:left!important}
td.lead{line-height:1.5}
.empty{color:var(--text-secondary);margin:2px 0 0}
.note{color:var(--text-muted);font-size:12.5px;margin:13px 0 0;line-height:1.55}
.hero{font-size:48px;font-weight:600;letter-spacing:-.03em;margin:0 0 10px;
  font-variant-numeric:tabular-nums;line-height:1}
.meter{height:9px;background:var(--surface-2);border-radius:5px;overflow:hidden}
.meter-fill{display:block;height:100%;background:var(--series-1);border-radius:5px;
  transition:width .4s ease}
.meter-unknown{background:repeating-linear-gradient(45deg,var(--line) 0 6px,transparent 6px 12px);width:100%!important}
.pairs{display:grid;grid-template-columns:1fr auto;gap:5px 16px;margin:16px 0 0;font-size:13.5px}
.pairs dt{color:var(--text-muted)}
.pairs dd{margin:0;text-align:right;font-variant-numeric:tabular-nums}
.bars{display:flex;flex-direction:column;gap:6px}
.bar-row{display:grid;grid-template-columns:88px 1fr 62px;align-items:center;gap:10px;
  border-radius:5px;padding:1px 0}
.bar-row:hover{background:var(--surface-2)}
.bar-name{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:11.5px;
  color:var(--text-secondary);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bar-track{background:var(--surface-2);border-radius:4px;height:15px;overflow:hidden}
.bar-fill{display:block;height:100%;border-radius:0 4px 4px 0;transition:width .4s ease}
.bar-value{font-size:12.5px;text-align:right;font-variant-numeric:tabular-nums;
  color:var(--text-secondary)}
.stack{display:flex;height:22px;border-radius:5px;overflow:hidden;background:var(--surface-2)}
.seg{display:block;height:100%}
.seg-1{background:var(--series-1)}
.seg-2{background:var(--series-2);border-left:2px solid var(--surface-1)}
.legend{display:flex;gap:18px;flex-wrap:wrap;margin-top:10px;font-size:13px;color:var(--text-secondary)}
.key{display:inline-flex;align-items:center;gap:7px}
.key b{color:var(--text-primary);font-variant-numeric:tabular-nums}
.swatch{width:10px;height:10px;border-radius:3px;display:inline-block}
.sw-1{background:var(--series-1)}
.sw-2{background:var(--series-2)}
.footnote{color:var(--text-muted);font-size:12.5px;margin:14px 0 0}
.empty{color:var(--text-muted);font-size:13.5px;margin:0}
.sources{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:7px}
.src{display:grid;grid-template-columns:14px 84px 52px 62px 1fr;align-items:baseline;gap:9px;
  font-size:13px;color:var(--text-secondary)}
.src-glyph{font-weight:700}
.src-good .src-glyph{color:var(--good)}
.src-critical .src-glyph{color:var(--critical)}
.src-name{color:var(--text-primary);font-size:12.5px}
.src-ms{text-align:right;font-variant-numeric:tabular-nums;color:var(--text-muted)}
.src-detail{color:var(--text-muted);overflow:hidden;text-overflow:ellipsis}
.table-wrap{margin-top:16px;overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:13px}
caption{text-align:left;color:var(--text-muted);font-size:12.5px;padding-bottom:8px}
th,td{text-align:right;padding:6px 9px;border-bottom:1px solid var(--line);
  font-variant-numeric:tabular-nums}
th:nth-child(2),td:nth-child(2){text-align:left}
th{color:var(--text-muted);font-weight:500}
td.mono{font-size:11.5px}
.raw-head{display:flex;justify-content:flex-end;margin-bottom:8px}
.raw{background:var(--surface-2);border-radius:8px;padding:13px;overflow:auto;max-height:380px;
  font-size:11.5px;margin:0}
#tip{position:fixed;z-index:20;pointer-events:none;background:var(--surface-2);
  color:var(--text-primary);border:1px solid var(--line);border-radius:7px;padding:6px 10px;
  font-size:12.5px;max-width:330px;box-shadow:0 6px 20px rgba(0,0,0,.35);opacity:0;
  transition:opacity .12s}
#tip.on{opacity:1}
footer{color:var(--text-muted);font-size:12.5px;margin-top:26px;border-top:1px solid var(--line);
  padding-top:14px}
@media(prefers-reduced-motion:reduce){*{transition:none!important}}
"""

_JS = """
(function(){
  var root=document.documentElement, KEY='solpulse-theme';
  try{var saved=localStorage.getItem(KEY); if(saved){root.setAttribute('data-theme',saved);}}catch(e){}
  var btn=document.getElementById('theme');
  function label(){
    var dark=root.getAttribute('data-theme')!=='light';
    btn.textContent=dark?'Light theme':'Dark theme';
    btn.setAttribute('aria-pressed',String(dark));
  }
  label();
  btn.addEventListener('click',function(){
    var next=root.getAttribute('data-theme')==='light'?'dark':'light';
    root.setAttribute('data-theme',next);
    try{localStorage.setItem(KEY,next);}catch(e){}
    label();
  });

  // Raw JSON is parsed from a data island, never interpolated into executable JS.
  var island=document.getElementById('snapshot-data');
  var code=document.getElementById('raw-code');
  if(island&&code){code.textContent=island.textContent.trim();}

  Array.prototype.forEach.call(document.querySelectorAll('.toggle'),function(b){
    b.addEventListener('click',function(){
      var el=document.getElementById(b.getAttribute('data-target'));
      if(!el)return;
      var open=el.hasAttribute('hidden');
      if(open){el.removeAttribute('hidden');}else{el.setAttribute('hidden','');}
      b.setAttribute('aria-expanded',String(open));
    });
  });

  var copy=document.querySelector('.copy');
  if(copy&&code){
    copy.addEventListener('click',function(){
      var done=function(){copy.textContent='Copied';setTimeout(function(){copy.textContent='Copy JSON';},1400);};
      if(navigator.clipboard){navigator.clipboard.writeText(code.textContent).then(done,function(){});}
      else{var s=window.getSelection(),r=document.createRange();r.selectNodeContents(code);
           s.removeAllRanges();s.addRange(r);done();}
    });
  }

  // Hover/focus tooltip layer. Hit target is the whole row, not the mark.
  var tip=document.createElement('div');
  tip.id='tip'; tip.setAttribute('role','tooltip');
  document.body.appendChild(tip);
  function show(el,x,y){
    var text=el.getAttribute('data-tip'); if(!text)return;
    tip.textContent=text; tip.classList.add('on');
    var box=tip.getBoundingClientRect();
    var left=Math.min(Math.max(8,x+14),window.innerWidth-box.width-8);
    var top=y-box.height-12; if(top<8){top=y+18;}
    tip.style.left=left+'px'; tip.style.top=top+'px';
  }
  function hide(){tip.classList.remove('on');}
  document.addEventListener('mousemove',function(e){
    var el=e.target.closest?e.target.closest('[data-tip]'):null;
    if(el){show(el,e.clientX,e.clientY);}else{hide();}
  });
  document.addEventListener('focusin',function(e){
    var el=e.target.closest?e.target.closest('[data-tip]'):null;
    if(el){var b=el.getBoundingClientRect();show(el,b.left+b.width/2,b.top);}else{hide();}
  });
  document.addEventListener('focusout',hide);
  document.addEventListener('scroll',hide,{passive:true});
})();
"""


def render_html(snap: dict, *, title: str = "Solana ecosystem dashboard") -> str:
    """Return one self-contained HTML document for this snapshot."""
    generated = snap.get("generated_at", UNKNOWN)
    return f"""<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} — {esc(generated)}</title>
<meta name="generator" content="{esc(snap.get('generator', 'solpulse'))}">
<meta name="description" content="Auto-updating snapshot of Solana network, validator and economic health.">
<style>{_CSS}</style>
</head>
<body>
<div class="wrap">
  <header class="top">
    <div>
      <h1>{esc(title)}</h1>
      <p class="stamp">Generated {esc(generated)} · {esc(snap.get("generator", "solpulse"))}
         · schema <span class="mono">{esc(snap.get("schema", UNKNOWN))}</span></p>
    </div>
    <button id="theme" type="button" aria-pressed="true">Light theme</button>
  </header>

  {_health_banner(snap)}
  {_kpi_row(snap)}

  <div class="grid">
    {_anomaly_card(snap)}
  </div>

  <div class="grid">
    {_epoch_meter(snap)}
    {_supply_chart(snap)}
  </div>
  <div class="grid">
    {_stake_chart(snap)}
  </div>
  <div class="grid">
    {_sources_card(snap)}
  </div>

  <footer>
    Collected straight from the Solana JSON-RPC API plus keyless public endpoints
    (CoinGecko, DefiLlama). No API keys, no accounts, and no dependencies beyond the
    Python standard library — this page included.
  </footer>
</div>
<script type="application/json" id="snapshot-data">{embed_json(snap)}</script>
<script>{_JS}</script>
</body>
</html>
"""
