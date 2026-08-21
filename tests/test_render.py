"""Renderer tests. No network, no clock — renderers are pure functions."""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import stat
import tempfile
import unittest
import unittest.mock

from solpulse import cli
from solpulse.cli import main
from solpulse.render import render_html, render_markdown
from solpulse.render.format import (
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
from solpulse.render.html import embed_json, esc


def snapshot_fixture(**overrides) -> dict:
    snap = {
        "schema": "solpulse/snapshot/v1",
        "generator": "solpulse 0.1.0",
        "generated_at": "2026-08-21T00:55:48Z",
        "sources": {
            "network": {
                "status": "ok",
                "elapsed_ms": 120.0,
                "data": {
                    "health": "ok",
                    "epoch": 1019,
                    "epoch_progress_pct": 86.28,
                    "epoch_slots_remaining": 59259,
                    "epoch_eta_seconds": 24610,
                    "absolute_slot": 440580741,
                    "block_height": 418630470,
                    "transaction_count": 540156925475,
                    "throughput": {
                        "tps_mean": 3964.26,
                        "tps_nonvote_mean": 2312.08,
                        "tps_latest": 3700.75,
                        "slot_time_ms_mean": 415.3,
                        "target_slot_time_ms": 400.0,
                        "sample_window_secs": 1800,
                    },
                },
            },
            "validators": {
                "status": "ok",
                "elapsed_ms": 900.0,
                "data": {
                    "active_count": 690,
                    "delinquent_count": 6,
                    "delinquent_pct": 0.86,
                    "delinquent_stake_pct": 0.001,
                    "delinquent_stake_sol": 5554.554,
                    "superminority_count": 18,
                    "top10_stake_share_pct": 24.38,
                    "median_commission_pct": 5,
                    "top_validators": [
                        {
                            "vote_pubkey": "CcaHc2L43ZWjwCHART3oZoJvHLAe9hzT2DJNUpBzoTN1",
                            "node_pubkey": "Fd7btgySsrjuo25CJCj7oE7VPMyezDhnx7pZkj2v69Nk",
                            "stake_sol": 17101526.856,
                            "stake_share_pct": 3.929,
                            "commission_pct": 7,
                        },
                        {
                            "vote_pubkey": "he1iusunGwqrNtafDtLdhsUQDFvo13z9sUa36PauBtk",
                            "node_pubkey": "HEL1USMZKAL2odpNBj2oCjffnFGaYwmbGmyewGv1e2TU",
                            "stake_sol": 16011570.341,
                            "stake_share_pct": 3.679,
                            "commission_pct": 0,
                        },
                    ],
                },
            },
            "economics": {
                "status": "ok",
                "elapsed_ms": 80.0,
                "data": {
                    "circulating_supply_sol": 583063301.407,
                    "non_circulating_supply_sol": 49449809.249,
                    "total_supply_sol": 632513110.656,
                    "circulating_pct": 92.18,
                    "inflation_total_pct": 3.688,
                    "inflation_validator_pct": 3.688,
                    "inflation_epoch": 1019,
                },
            },
            "price": {
                "status": "ok",
                "elapsed_ms": 210.0,
                "data": {
                    "sol_usd": 88.23,
                    "sol_usd_24h_change_pct": 3.74,
                    "sol_market_cap_usd": 51444387824.257034,
                    "source": "coingecko",
                },
            },
            "tvl": {
                "status": "ok",
                "elapsed_ms": 330.0,
                "data": {
                    "tvl_usd": 5236869703.0,
                    "tvl_change_1d_pct": -0.0,
                    "tvl_change_7d_pct": 8.26,
                    "tvl_change_30d_pct": 4.7,
                    "source": "defillama",
                },
            },
        },
        "summary": {
            "sources_total": 5,
            "sources_ok": 5,
            "sources_failed": 0,
            "failed_sources": [],
            "complete": True,
        },
    }
    snap.update(overrides)
    return snap


def degraded_fixture() -> dict:
    """Every source failed — the "we could not find out" case."""
    snap = snapshot_fixture()
    for name in snap["sources"]:
        snap["sources"][name] = {
            "status": "error",
            "error": "HttpError: connection refused",
            "elapsed_ms": 5.0,
        }
    snap["summary"] = {
        "sources_total": 5,
        "sources_ok": 0,
        "sources_failed": 5,
        "failed_sources": sorted(snap["sources"]),
        "complete": False,
    }
    return snap


class FormatTests(unittest.TestCase):
    def test_missing_values_render_as_unknown_not_zero(self):
        for fn in (num, compact, usd, usd_compact, pct, signed_pct, duration):
            for missing in (None, "n/a", {}, []):
                self.assertEqual(fn(missing), "unknown", f"{fn.__name__}({missing!r})")

    def test_booleans_are_not_numbers(self):
        # bool is an int subclass; True must not format as "1".
        self.assertEqual(num(True), "unknown")

    def test_real_zero_is_kept_distinct_from_unknown(self):
        self.assertEqual(num(0), "0")
        self.assertEqual(pct(0), "0.00%")
        self.assertEqual(signed_pct(0), "+0.00%")

    def test_compact_magnitudes(self):
        self.assertEqual(compact(5236869703), "5.24B")
        self.assertEqual(compact(1500), "1.50K")
        self.assertEqual(compact(999), "999.00")
        self.assertEqual(compact(-2_000_000), "-2.00M")

    def test_duration(self):
        self.assertEqual(duration(24610), "6h 50m")
        self.assertEqual(duration(90), "1m")
        self.assertEqual(duration(-5), "unknown")

    def test_direction_and_signs(self):
        self.assertEqual(direction(3.74), "up")
        self.assertEqual(direction(-1), "down")
        self.assertEqual(direction(0), "flat")
        self.assertEqual(direction(None), "unknown")
        self.assertEqual(signed_pct(-2.5), "-2.50%")

    def test_shorten_keeps_short_ids_whole(self):
        self.assertEqual(shorten("abc"), "abc")
        self.assertIn("…", shorten("C" * 44))
        self.assertEqual(shorten(None), "unknown")


class MarkdownTests(unittest.TestCase):
    def test_reports_every_section(self):
        md = render_markdown(snapshot_fixture())
        for heading in ("# Solana ecosystem report", "## At a glance", "## Network performance",
                        "## Validators", "## Economics", "## Data sources"):
            self.assertIn(heading, md)

    def test_real_numbers_are_formatted_not_raw(self):
        md = render_markdown(snapshot_fixture())
        self.assertIn("86.28%", md)
        self.assertIn("690", md)
        self.assertIn("$88.23", md)
        self.assertIn("$5.24B", md)
        self.assertIn("5 of 5", md)

    def test_deltas_carry_arrow_and_sign(self):
        md = render_markdown(snapshot_fixture())
        self.assertIn("▲ +3.74%", md)

    def test_failed_sources_render_unknown_and_surface_the_error(self):
        md = render_markdown(degraded_fixture())
        self.assertIn("unknown", md)
        self.assertIn("connection refused", md)
        self.assertIn("0 of 5", md)
        self.assertNotIn("$88.23", md)

    def test_deterministic(self):
        snap = snapshot_fixture()
        self.assertEqual(render_markdown(snap), render_markdown(snap))


class HtmlTests(unittest.TestCase):
    def test_document_shape_and_dark_default(self):
        page = render_html(snapshot_fixture())
        self.assertTrue(page.startswith("<!DOCTYPE html>"))
        self.assertIn('data-theme="dark"', page)
        self.assertIn("</html>", page)

    def test_is_self_contained_no_external_requests(self):
        page = render_html(snapshot_fixture())
        for scheme in ("http://", "https://", "//cdn", "<link"):
            self.assertNotIn(scheme, page, f"page reaches outside for {scheme}")

    def test_chain_supplied_strings_are_escaped(self):
        snap = snapshot_fixture()
        hostile = '<script>alert(1)</script>"onload="x'
        snap["sources"]["validators"]["data"]["top_validators"][0]["vote_pubkey"] = hostile
        page = render_html(snap)
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;", page)

    def test_embedded_json_cannot_break_out_of_its_script_tag(self):
        payload = embed_json({"evil": "</script><script>alert(1)</script>"})
        self.assertNotIn("</script>", payload)
        self.assertNotIn("<", payload)
        self.assertEqual(json.loads(payload)["evil"], "</script><script>alert(1)</script>")

    def test_embedded_json_round_trips_the_whole_snapshot(self):
        snap = snapshot_fixture()
        page = render_html(snap)
        start = page.index('id="snapshot-data">') + len('id="snapshot-data">')
        end = page.index("</script>", start)
        self.assertEqual(json.loads(page[start:end]), snap)

    def test_meter_width_is_clamped_to_the_track(self):
        snap = snapshot_fixture()
        snap["sources"]["network"]["data"]["epoch_progress_pct"] = 143.0
        self.assertIn("width:100.00%", render_html(snap))
        snap["sources"]["network"]["data"]["epoch_progress_pct"] = -8.0
        self.assertIn("width:0.00%", render_html(snap))

    def test_unknown_progress_does_not_render_a_zero_bar(self):
        snap = snapshot_fixture()
        snap["sources"]["network"]["data"]["epoch_progress_pct"] = None
        page = render_html(snap)
        self.assertIn("meter-unknown", page)
        self.assertIn("unknown", page)

    def test_every_chart_has_a_table_or_pair_view(self):
        page = render_html(snapshot_fixture())
        self.assertIn('id="stake-table"', page)
        self.assertIn("<table>", page)
        self.assertIn("Table view", page)

    def test_status_is_never_color_alone(self):
        page = render_html(snapshot_fixture())
        self.assertIn("Healthy", page)      # a word, not just a green border
        self.assertIn("banner-glyph", page)  # and a glyph

    def test_degraded_snapshot_renders_without_inventing_numbers(self):
        page = render_html(degraded_fixture())
        self.assertIn("unknown", page)
        self.assertIn("connection refused", page)
        self.assertNotIn("$88.23", page)
        # Collapse whitespace so the assertion tracks the text, not the indentation.
        self.assertIn("0 of 5 returned data", re.sub(r"\s+", " ", page))

    def test_unhealthy_network_reads_degraded(self):
        snap = snapshot_fixture()
        snap["sources"]["network"]["data"]["health"] = "behind by 900 slots"
        page = render_html(snap)
        self.assertIn("Degraded", page)
        self.assertIn("banner-critical", page)

    def test_missing_validators_does_not_crash_the_chart(self):
        snap = snapshot_fixture()
        snap["sources"]["validators"] = {"status": "error", "error": "boom", "elapsed_ms": 1.0}
        page = render_html(snap)
        self.assertIn("Stake by validator", page)
        self.assertIn("unknown", page)

    def test_esc_handles_none(self):
        self.assertEqual(esc(None), "unknown")


def read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def run_cli(argv: list[str]) -> tuple[int, str]:
    """Run the CLI with stdout captured, so tests stay quiet."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        rc = main(argv)
    return rc, buffer.getvalue()


class RenderCommandTests(unittest.TestCase):
    def test_render_writes_every_requested_format_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "snap.json")
            with open(src, "w", encoding="utf-8") as fh:
                json.dump(snapshot_fixture(), fh)
            md, page = os.path.join(tmp, "r.md"), os.path.join(tmp, "d.html")
            rc, _ = run_cli(["render", src, "--markdown", md, "--html", page])
            self.assertEqual(rc, 0)
            self.assertIn("## At a glance", read(md))
            self.assertIn("<!DOCTYPE html>", read(page))

    def test_render_defaults_to_markdown_on_stdout(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "snap.json")
            with open(src, "w", encoding="utf-8") as fh:
                json.dump(snapshot_fixture(), fh)
            rc, out = run_cli(["render", src])
            self.assertEqual(rc, 0)
            self.assertTrue(out.startswith("# Solana ecosystem report"))

    def test_render_reads_stdin(self):
        stdin = io.StringIO(json.dumps(snapshot_fixture()))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            with unittest.mock.patch("sys.stdin", stdin):
                rc = main(["render", "-"])
        self.assertEqual(rc, 0)
        self.assertIn("## At a glance", out.getvalue())

    def test_render_exits_nonzero_on_a_degraded_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "snap.json")
            with open(src, "w", encoding="utf-8") as fh:
                json.dump(degraded_fixture(), fh)
            rc, _ = run_cli(["render", src, "--html", os.path.join(tmp, "d.html")])
            self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()


class AnomalyRenderTest(unittest.TestCase):
    """The anomaly section must never imply a check that did not happen."""

    def report(self, anomalies, **baseline):
        base = {"records": 8, "min_required": 5, "window": 30, "sufficient": True,
                "first": "2026-08-20T01:00:00Z", "last": "2026-08-21T05:00:00Z"}
        base.update(baseline)
        counts = {"critical": 0, "warn": 0, "info": 0}
        for item in anomalies:
            counts[item["severity"]] += 1
        return {"schema": "solpulse/anomalies/v1", "anomalies": anomalies,
                "counts": counts, "worst": anomalies[0]["severity"] if anomalies else None,
                "baseline": base, "checked": ["tps_mean", "sol_usd"], "skipped": {}}

    def test_snapshot_without_history_renders_no_anomaly_section(self):
        snap = snapshot_fixture()
        self.assertNotIn("anomalies", snap)
        # An empty card would read as "checked, all clear" — which would be a lie.
        self.assertNotIn("## Anomalies", render_markdown(snap))
        self.assertNotIn("Anomaly detection", render_html(snap))

    def test_clean_report_says_none_found_and_names_the_baseline(self):
        snap = snapshot_fixture(anomalies=self.report([]))
        md = render_markdown(snap)
        self.assertIn("## Anomalies", md)
        self.assertIn("No anomalies detected", md)
        self.assertIn("8 prior record(s)", md)
        self.assertIn("No anomalies detected", render_html(snap))

    def test_insufficient_baseline_is_disclosed_in_both_formats(self):
        snap = snapshot_fixture(anomalies=self.report([], records=2, sufficient=False))
        md = render_markdown(snap)
        self.assertIn("Deviation rules did not run", md)
        self.assertIn("threshold rules", md.lower())
        self.assertIn("Deviation rules did not run", render_html(snap))

    def test_findings_carry_glyph_and_word_not_color_alone(self):
        snap = snapshot_fixture(anomalies=self.report([
            {"code": "network_unhealthy", "severity": "critical", "metric": "health",
             "message": "RPC node reports health 'behind', not 'ok'.", "value": "behind"},
        ]))
        html_out = render_html(snap)
        self.assertIn("Critical", html_out)      # the word
        self.assertIn("‼", html_out)             # the glyph
        md = render_markdown(snap)
        self.assertIn("CRITICAL", md)

    def test_null_sigma_renders_as_a_dash_not_as_zero(self):
        snap = snapshot_fixture(anomalies=self.report([
            {"code": "deviation", "severity": "warn", "metric": "sol_usd",
             "message": "SOL price is 200.00 USD, 124.7% above normal.",
             "deviation_sigma": None, "value": 200.0},
        ]))
        self.assertIn("—", render_markdown(snap))
        self.assertNotIn("0.0σ", render_markdown(snap))
        self.assertNotIn("0.0σ", render_html(snap))

    def test_anomaly_message_is_html_escaped(self):
        snap = snapshot_fixture(anomalies=self.report([
            {"code": "network_unhealthy", "severity": "critical", "metric": "<img src=x>",
             "message": "<script>alert(1)</script>", "value": 1},
        ]))
        out = render_html(snap)
        self.assertNotIn("<script>alert(1)</script>", out)
        self.assertIn("&lt;script&gt;", out)

    def test_html_with_anomalies_still_loads_nothing_external(self):
        snap = snapshot_fixture(anomalies=self.report([
            {"code": "deviation", "severity": "warn", "metric": "tps_mean",
             "message": "total throughput is unusual.", "deviation_sigma": 4.2},
        ]))
        out = render_html(snap)
        for pattern in ("src=\"http", "href=\"http", "@import", "//cdn"):
            self.assertNotIn(pattern, out)


class HistoryCliTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.log = os.path.join(self.dir, "history.jsonl")
        snap = snapshot_fixture()
        from solpulse import history as hist
        for stamp in ("2026-08-21T01:00:00Z", "2026-08-21T05:00:00Z"):
            hist.append(self.log, dict(snap, generated_at=stamp))

    def run_cli(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(argv)
        return code, out.getvalue()

    def test_history_lists_records(self):
        code, out = self.run_cli(["history", self.log])
        self.assertEqual(code, 0)
        self.assertIn("2 record(s)", out)
        self.assertIn("2026-08-21T05:00:00Z", out)

    def test_history_prints_one_metric_as_a_series(self):
        code, out = self.run_cli(["history", self.log, "--metric", "sol_usd"])
        self.assertEqual(code, 0)
        self.assertIn("sol_usd", out)
        self.assertEqual(out.count("2026-08-21"), 2)  # one line per recorded point

    def test_unknown_metric_exits_nonzero_and_lists_the_known_ones(self):
        code, out = self.run_cli(["history", self.log, "--metric", "nonsense"])
        self.assertEqual(code, 1)
        self.assertIn("known metrics", out)

    def test_missing_log_exits_nonzero(self):
        code, out = self.run_cli(["history", os.path.join(self.dir, "nope.jsonl")])
        self.assertEqual(code, 1)
        self.assertIn("no records", out)

    def test_render_recomputes_anomalies_against_a_history_log(self):
        snap_path = os.path.join(self.dir, "snap.json")
        md_path = os.path.join(self.dir, "report.md")
        with open(snap_path, "w", encoding="utf-8") as fh:
            json.dump(snapshot_fixture(), fh)
        code, _ = self.run_cli(["render", snap_path, "--history", self.log, "--markdown", md_path])
        self.assertIn(code, (0, 1))
        with open(md_path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("## Anomalies", text)
        # Only 2 records, so the report must admit the baseline is too short.
        self.assertIn("Deviation rules did not run", text)


class AtomicWriteTest(unittest.TestCase):
    """Output files are replaced atomically.

    A hosted dashboard is regenerated on a timer while a web server serves the
    previous copy. Writing in place would let a reader see a truncated file.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "dashboard.html")

    def test_write_produces_the_full_content(self):
        cli._write(self.path, "hello\n")
        with open(self.path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "hello\n")

    def test_overwrite_replaces_rather_than_truncating_in_place(self):
        cli._write(self.path, "x" * 5000)
        before = os.stat(self.path).st_ino
        cli._write(self.path, "y" * 10)
        after = os.stat(self.path).st_ino
        # os.replace swaps in a different file; an in-place rewrite would keep
        # the inode and would therefore have been observable half-written.
        self.assertNotEqual(before, after)
        with open(self.path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "y" * 10)

    def test_a_failed_write_leaves_the_previous_file_intact(self):
        cli._write(self.path, "good content")

        class Boom(Exception):
            pass

        original = cli.os.replace

        def fail(*args, **kwargs):
            raise Boom("disk full")

        cli.os.replace = fail
        try:
            with self.assertRaises(Boom):
                cli._write(self.path, "bad content")
        finally:
            cli.os.replace = original

        with open(self.path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "good content")

    def test_a_failed_write_leaves_no_temporary_file_behind(self):
        original = cli.os.replace

        def fail(*args, **kwargs):
            raise OSError("nope")

        cli.os.replace = fail
        try:
            with self.assertRaises(OSError):
                cli._write(self.path, "content")
        finally:
            cli.os.replace = original

        self.assertEqual(os.listdir(self.dir), [])

    def test_temporary_file_is_created_beside_the_target_not_in_tmp(self):
        """os.replace is only atomic within one filesystem; /tmp is often another."""
        seen = {}
        original = tempfile.mkstemp

        def spy(*args, **kwargs):
            seen["dir"] = kwargs.get("dir")
            return original(*args, **kwargs)

        cli.tempfile.mkstemp = spy
        try:
            cli._write(self.path, "content")
        finally:
            cli.tempfile.mkstemp = original

        self.assertEqual(os.path.abspath(seen["dir"]), os.path.abspath(self.dir))

    def test_output_is_readable_by_other_users_not_mkstemp_0600(self):
        """A dashboard served by nginx as another user must not be mode 0600."""
        expected_umask = os.umask(0o022)
        os.umask(expected_umask)
        cli._write(self.path, "content")
        mode = stat.S_IMODE(os.stat(self.path).st_mode)
        self.assertEqual(mode, 0o666 & ~expected_umask)
        self.assertTrue(mode & stat.S_IROTH, "world-readable under a 022 umask")


class DigestOutputTest(unittest.TestCase):
    """Every format the tool prints must also be re-renderable offline."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.snap = os.path.join(self.dir, "snap.json")
        with open(self.snap, "w", encoding="utf-8") as fh:
            json.dump(snapshot_fixture(), fh)

    def test_render_writes_the_text_digest_to_a_file(self):
        out = os.path.join(self.dir, "digest.txt")
        with contextlib.redirect_stdout(io.StringIO()):
            main(["render", self.snap, "--digest", out])
        text = open(out, encoding="utf-8").read()
        self.assertIn("solpulse — Solana ecosystem snapshot", text)
        self.assertIn("Sources:", text)

    def test_digest_destination_suppresses_the_markdown_stdout_default(self):
        out = os.path.join(self.dir, "digest.txt")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            main(["render", self.snap, "--digest", out])
        # Asking for a file is asking for a file; stdout must not also become
        # a full Markdown report.
        self.assertNotIn("# Solana ecosystem report", buf.getvalue())
