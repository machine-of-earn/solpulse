"""Command line entry point: `python3 -m solpulse.cli collect`."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

from . import __version__, history
from .anomaly import detect
from .render import render_html, render_markdown
from .rpc import MAINNET_BETA
from .snapshot import get, snapshot


def _human(snap: dict) -> str:
    """One-screen text digest — the fast 'is Solana OK right now' view."""
    lines = [f"solpulse — Solana ecosystem snapshot @ {snap['generated_at']}", ""]

    def row(label: str, value, unit: str = "") -> None:
        shown = "unknown" if value is None else f"{value}{unit}"
        lines.append(f"  {label:<28} {shown}")

    lines.append("Network")
    row("health", get(snap, "network", "health"))
    row("epoch", get(snap, "network", "epoch"))
    row("epoch progress", get(snap, "network", "epoch_progress_pct"), "%")
    row("block height", get(snap, "network", "block_height"))
    row("TPS (all)", get(snap, "network", "throughput", "tps_mean"))
    row("TPS (non-vote)", get(snap, "network", "throughput", "tps_nonvote_mean"))
    row("slot time", get(snap, "network", "throughput", "slot_time_ms_mean"), " ms")

    lines += ["", "Validators"]
    row("active", get(snap, "validators", "active_count"))
    row("delinquent", get(snap, "validators", "delinquent_count"))
    row("delinquent stake", get(snap, "validators", "delinquent_stake_pct"), "%")
    row("superminority", get(snap, "validators", "superminority_count"))
    row("top-10 stake share", get(snap, "validators", "top10_stake_share_pct"), "%")

    lines += ["", "Economics"]
    row("SOL price", get(snap, "price", "sol_usd"), " USD")
    row("SOL 24h change", get(snap, "price", "sol_usd_24h_change_pct"), "%")
    row("DeFi TVL", get(snap, "tvl", "tvl_usd"), " USD")
    row("TVL 7d change", get(snap, "tvl", "tvl_change_7d_pct"), "%")
    row("circulating supply", get(snap, "economics", "circulating_supply_sol"), " SOL")
    row("inflation", get(snap, "economics", "inflation_total_pct"), "%")

    report = snap.get("anomalies")
    if report:
        found = report["anomalies"]
        base = report["baseline"]
        lines += ["", f"Anomalies ({len(found)}) — baseline {base['records']} prior record(s)"]
        if not base["sufficient"]:
            lines.append(
                f"  baseline too short for deviation rules (need {base['min_required']});"
                " threshold rules still applied"
            )
        if not found:
            lines.append("  none")
        for item in found:
            lines.append(f"  [{item['severity'].upper():<8}] {item['message']}")

    summary = snap["summary"]
    lines += ["", f"Sources: {summary['sources_ok']}/{summary['sources_total']} ok"]
    if summary["failed_sources"]:
        lines.append(f"Failed:  {', '.join(summary['failed_sources'])}")
        for name in summary["failed_sources"]:
            lines.append(f"  {name}: {snap['sources'][name]['error']}")
    return "\n".join(lines)


def _write(path: str, text: str) -> None:
    """Write `text` to `path` atomically.

    A hosted dashboard is regenerated on a timer while a web server is serving
    the previous copy of the same file. Writing in place means a browser that
    reloads mid-write gets a truncated document. So: write a temporary file
    alongside the target, flush it to disk, then `os.replace` it into place —
    which is atomic on POSIX and on Windows. A reader sees either the whole old
    file or the whole new one, never a half-written one.

    The temporary file is created in the *target's* directory, because
    `os.replace` is only atomic within a single filesystem and /tmp is
    routinely a different one. On failure the temporary file is cleaned up and
    the existing output is left untouched.
    """
    target = os.path.abspath(path)
    directory = os.path.dirname(target) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".solpulse-", suffix=".tmp")
    try:
        # mkstemp is deliberately 0600. That is wrong for these outputs: a
        # dashboard written by a timer and served by a web server running as
        # another user would become unreadable (403). Restore the mode the
        # process's umask would have produced for a normal file.
        umask = os.umask(0)
        os.umask(umask)
        os.chmod(tmp, 0o666 & ~umask)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _emit(snap: dict, args) -> None:
    """Write every requested output format from the one collected snapshot.

    All formats come from a single collection run, so the JSON, Markdown and
    HTML for a given timestamp can never disagree with each other.
    """
    if args.out:
        _write(args.out, json.dumps(snap, indent=2, sort_keys=True) + "\n")
    if args.markdown:
        _write(args.markdown, render_markdown(snap))
    if args.html:
        _write(args.html, render_html(snap))
    if args.digest:
        _write(args.digest, _human(snap) + "\n")


def _add_output_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="print the raw snapshot JSON")
    parser.add_argument("--markdown", metavar="PATH", help="write the Markdown report to PATH")
    parser.add_argument("--html", metavar="PATH", help="write the HTML dashboard to PATH")
    parser.add_argument("--digest", metavar="PATH", help="write the plain-text digest to PATH")


def _history_command(args) -> int:
    """Read back the log: either the whole record set, or one metric as a series."""
    records = history.load(args.path)
    if not records:
        print(f"no records in {args.path}")
        return 1
    shown = records[-args.limit:] if args.limit and args.limit > 0 else records

    if args.json:
        print(json.dumps(shown, indent=2, sort_keys=True))
        return 0

    if args.metric:
        points = history.series(shown, args.metric)
        if not points:
            known = ", ".join(sorted(history.METRICS))
            print(f"metric {args.metric!r} has no recorded values. known metrics: {known}")
            return 1
        print(f"{args.metric} — {len(points)} point(s) of {len(records)} record(s)")
        for stamp, value in points:
            print(f"  {stamp}  {value:>16,.4f}")
        return 0

    print(f"{len(records)} record(s) in {args.path}, showing last {len(shown)}")
    for record in shown:
        metrics = record.get("metrics") or {}
        failed = record.get("failed_sources") or []
        note = f" failed={','.join(sorted(failed))}" if failed else ""
        print(
            f"  {record.get('generated_at')}  health={record.get('health') or 'unknown'}"
            f"  sources={record.get('sources_ok')}/{record.get('sources_total')}"
            f"  metrics={len(metrics)}{note}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="solpulse", description=__doc__)
    parser.add_argument("--version", action="version", version=f"solpulse {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    collect = sub.add_parser("collect", help="fetch a snapshot of Solana's current state")
    collect.add_argument("--endpoint", default=MAINNET_BETA, help="Solana JSON-RPC endpoint")
    collect.add_argument("--timeout", type=float, default=20.0, help="per-request timeout in seconds")
    collect.add_argument("--out", metavar="PATH", help="write the snapshot JSON to PATH")
    collect.add_argument(
        "--history",
        metavar="PATH",
        help="append this run to a JSONL history log at PATH and detect anomalies "
             "against the records already in it",
    )
    _add_output_flags(collect)

    render = sub.add_parser(
        "render", help="re-render a saved snapshot JSON without touching the network"
    )
    render.add_argument("snapshot", help="path to a snapshot JSON file, or - for stdin")
    render.add_argument("--out", metavar="PATH", help="write the snapshot JSON back out to PATH")
    render.add_argument(
        "--history",
        metavar="PATH",
        help="recompute anomalies against this history log instead of using the "
             "report already embedded in the snapshot",
    )
    _add_output_flags(render)

    hist = sub.add_parser("history", help="inspect a recorded history log")
    hist.add_argument("path", help="path to the JSONL history log")
    hist.add_argument("--metric", help="print one metric as a time series")
    hist.add_argument("--limit", type=int, default=20, help="how many records to show")
    hist.add_argument("--json", action="store_true", help="print the records as JSON")

    args = parser.parse_args(argv)

    if args.command == "collect":
        snap = snapshot(endpoint=args.endpoint, timeout=args.timeout)
        if args.history:
            # Detect against the records written *before* this run, then append,
            # so a snapshot is never part of the baseline it is judged against.
            snap["anomalies"] = detect(snap, history.load(args.history))
            history.append(args.history, snap)
        _emit(snap, args)
        print(json.dumps(snap, indent=2, sort_keys=True) if args.json else _human(snap))
        # Exit 1 when any source failed, so cron/CI notices a degraded report.
        return 0 if snap["summary"]["complete"] else 1

    if args.command == "render":
        if args.snapshot == "-":
            snap = json.load(sys.stdin)
        else:
            with open(args.snapshot, encoding="utf-8") as fh:
                snap = json.load(fh)
        if args.history:
            snap["anomalies"] = detect(snap, history.load(args.history))
        _emit(snap, args)
        if args.json:
            print(json.dumps(snap, indent=2, sort_keys=True))
        elif not (args.markdown or args.html or args.out or args.digest):
            # No destination asked for: Markdown to stdout is the useful default.
            print(render_markdown(snap), end="")
        else:
            print(_human(snap))
        return 0 if snap.get("summary", {}).get("complete") else 1

    if args.command == "history":
        return _history_command(args)

    return 2


if __name__ == "__main__":
    sys.exit(main())
