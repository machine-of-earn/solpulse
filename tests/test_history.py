"""History store and anomaly detection. No network, no clock, no real files
outside a temp dir."""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from solpulse import anomaly, history


def snap(generated_at="2026-08-21T09:00:00Z", **metrics) -> dict:
    """A snapshot carrying just the metrics a test cares about."""
    net = {
        "health": metrics.pop("health", "ok"),
        "epoch": metrics.pop("epoch", 1019),
        "epoch_progress_pct": metrics.pop("epoch_progress_pct", 90.0),
        "throughput": {
            "tps_mean": metrics.pop("tps_mean", 3000.0),
            "tps_nonvote_mean": metrics.pop("tps_nonvote_mean", 1700.0),
            "slot_time_ms_mean": metrics.pop("slot_time_ms_mean", 400.0),
        },
    }
    vals = {
        "active_count": metrics.pop("active_count", 690),
        "delinquent_count": metrics.pop("delinquent_count", 6),
        "delinquent_stake_pct": metrics.pop("delinquent_stake_pct", 0.05),
        "superminority_count": metrics.pop("superminority_count", 22),
        "top10_stake_share_pct": metrics.pop("top10_stake_share_pct", 15.0),
    }
    price = {"sol_usd": metrics.pop("sol_usd", 89.0)}
    failed = metrics.pop("failed_sources", [])
    sources = {
        "network": {"status": "ok", "data": net},
        "validators": {"status": "ok", "data": vals},
        "price": {"status": "ok", "data": price},
    }
    for name in failed:
        sources[name] = {"status": "error", "error": "HttpError: boom"}
    return {
        "schema": "solpulse/snapshot/v1",
        "generated_at": generated_at,
        "sources": sources,
        "summary": {
            "sources_ok": len(sources) - len(failed),
            "sources_total": len(sources),
            "failed_sources": list(failed),
            "complete": not failed,
        },
    }


class RecordTest(unittest.TestCase):
    def test_flattens_only_measured_numeric_metrics(self):
        record = history.record_from_snapshot(snap())
        self.assertEqual(record["metrics"]["tps_nonvote_mean"], 1700.0)
        self.assertEqual(record["health"], "ok")
        # Nothing supplies these sources in the fixture, so they must be absent
        # rather than present as zero.
        self.assertNotIn("tvl_usd", record["metrics"])
        self.assertNotIn("circulating_supply_sol", record["metrics"])

    def test_failed_source_metric_is_absent_not_zero(self):
        record = history.record_from_snapshot(snap(failed_sources=["price"]))
        self.assertNotIn("sol_usd", record["metrics"])
        self.assertEqual(record["failed_sources"], ["price"])

    def test_real_zero_is_kept(self):
        record = history.record_from_snapshot(snap(delinquent_count=0))
        self.assertIn("delinquent_count", record["metrics"])
        self.assertEqual(record["metrics"]["delinquent_count"], 0)

    def test_booleans_are_not_numbers(self):
        self.assertFalse(history._is_num(True))


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "nested", "history.jsonl")

    def test_append_creates_dirs_and_round_trips(self):
        history.append(self.path, snap(generated_at="2026-08-21T01:00:00Z"))
        history.append(self.path, snap(generated_at="2026-08-21T05:00:00Z"))
        records = history.load(self.path)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["generated_at"], "2026-08-21T01:00:00Z")
        self.assertEqual(records[-1]["generated_at"], "2026-08-21T05:00:00Z")

    def test_missing_file_is_empty_history_not_an_error(self):
        self.assertEqual(history.load(os.path.join(self.dir, "nope.jsonl")), [])

    def test_truncated_final_line_does_not_destroy_the_history(self):
        history.append(self.path, snap(generated_at="2026-08-21T01:00:00Z"))
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write('{"schema": "solpulse/record/v1", "metr')  # killed mid-write
        records = history.load(self.path)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["generated_at"], "2026-08-21T01:00:00Z")

    def test_series_skips_records_missing_the_metric(self):
        history.append(self.path, snap(generated_at="2026-08-21T01:00:00Z"))
        history.append(self.path, snap(generated_at="2026-08-21T05:00:00Z", failed_sources=["price"]))
        records = history.load(self.path)
        self.assertEqual(len(history.series(records, "sol_usd")), 1)
        self.assertEqual(len(history.series(records, "tps_mean")), 2)

    def test_limit_returns_the_most_recent(self):
        for hour in range(1, 5):
            history.append(self.path, snap(generated_at=f"2026-08-21T0{hour}:00:00Z"))
        recent = history.load(self.path, limit=2)
        self.assertEqual([r["generated_at"] for r in recent],
                         ["2026-08-21T03:00:00Z", "2026-08-21T04:00:00Z"])


class StatisticsTest(unittest.TestCase):
    def test_median_odd_and_even(self):
        self.assertEqual(anomaly.median([3, 1, 2]), 2)
        self.assertEqual(anomaly.median([4, 1, 2, 3]), 2.5)
        self.assertIsNone(anomaly.median([]))

    def test_mad_ignores_a_single_outlier(self):
        steady = [100, 101, 99, 100, 102]
        spiked = steady + [100000]
        # A standard deviation would explode here; the MAD barely moves.
        self.assertLess(abs(anomaly.mad(spiked) - anomaly.mad(steady)), 2.0)

    def test_flat_baseline_has_no_measurable_spread(self):
        self.assertIsNone(anomaly.robust_sigma([5, 5, 5, 5, 5]))


class ThresholdTest(unittest.TestCase):
    """Threshold rules must fire with no history at all."""

    def test_unhealthy_node_is_critical_on_first_ever_run(self):
        report = anomaly.detect(snap(health="behind"), [])
        codes = [a["code"] for a in report["anomalies"]]
        self.assertIn("network_unhealthy", codes)
        self.assertEqual(report["worst"], "critical")

    def test_failed_source_is_reported(self):
        report = anomaly.detect(snap(failed_sources=["price"]), [])
        found = [a for a in report["anomalies"] if a["code"] == "source_failure"]
        self.assertEqual(len(found), 1)
        self.assertIn("not zero", found[0]["message"])

    def test_delinquent_stake_tiers(self):
        warn = anomaly.detect(snap(delinquent_stake_pct=7.0), [])["anomalies"]
        self.assertIn("delinquent_stake_elevated", [a["code"] for a in warn])
        crit = anomaly.detect(snap(delinquent_stake_pct=20.0), [])["anomalies"]
        entry = [a for a in crit if a["code"] == "delinquent_stake_high"][0]
        self.assertEqual(entry["severity"], "critical")

    def test_slot_time_tiers(self):
        self.assertIn("slot_time_elevated",
                      [a["code"] for a in anomaly.detect(snap(slot_time_ms_mean=650), [])["anomalies"]])
        self.assertIn("slot_time_high",
                      [a["code"] for a in anomaly.detect(snap(slot_time_ms_mean=900), [])["anomalies"]])

    def test_healthy_chain_with_no_history_reports_nothing_alarming(self):
        report = anomaly.detect(snap(superminority_count=25), [])
        self.assertEqual(report["anomalies"], [])
        self.assertIsNone(report["worst"])


class DeviationTest(unittest.TestCase):
    def baseline(self, count=8, **kwargs):
        return [history.record_from_snapshot(snap(generated_at=f"t{i}", **kwargs))
                for i in range(count)]

    def test_short_history_skips_deviation_rules_and_says_so(self):
        report = anomaly.detect(snap(tps_nonvote_mean=99999.0), self.baseline(3))
        self.assertFalse(report["baseline"]["sufficient"])
        self.assertEqual([a["code"] for a in report["anomalies"]], [])
        self.assertIn("need 5", report["skipped"]["tps_nonvote_mean"])

    def test_large_move_against_a_varied_baseline_is_flagged(self):
        base = [history.record_from_snapshot(snap(generated_at=f"t{i}", tps_nonvote_mean=1700.0 + i))
                for i in range(8)]
        report = anomaly.detect(snap(tps_nonvote_mean=200.0), base)
        found = [a for a in report["anomalies"] if a.get("metric") == "tps_nonvote_mean"]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["direction"], "down")
        self.assertEqual(found[0]["severity"], "critical")
        self.assertGreater(found[0]["deviation_sigma"], anomaly.CRITICAL_SIGMA)

    def test_flat_baseline_reports_null_sigma_not_infinity(self):
        report = anomaly.detect(snap(sol_usd=200.0), self.baseline(8))
        found = [a for a in report["anomalies"] if a.get("metric") == "sol_usd"][0]
        self.assertIsNone(found["deviation_sigma"])
        self.assertIn("no spread", found["message"])
        # And it must be JSON-serialisable — Infinity would not round-trip.
        self.assertIn('"deviation_sigma": null', json.dumps(found, indent=1))

    def test_tiny_move_below_the_relative_gate_is_not_flagged(self):
        base = [history.record_from_snapshot(snap(generated_at=f"t{i}", sol_usd=89.0 + i * 0.001))
                for i in range(8)]
        # +1% on a metric whose gate is 5%: statistically extreme, practically noise.
        report = anomaly.detect(snap(sol_usd=89.9), base)
        self.assertEqual([a for a in report["anomalies"] if a.get("metric") == "sol_usd"], [])

    def test_metric_missing_from_this_snapshot_is_skipped_not_treated_as_zero(self):
        report = anomaly.detect(snap(failed_sources=["price"]), self.baseline(8))
        self.assertEqual([a for a in report["anomalies"] if a.get("metric") == "sol_usd"], [])
        self.assertIn("not measured", report["skipped"]["sol_usd"])

    def test_current_snapshot_is_not_part_of_its_own_baseline(self):
        base = [history.record_from_snapshot(snap(generated_at=f"t{i}", tps_nonvote_mean=1700.0 + i))
                for i in range(8)]
        before = anomaly.detect(snap(tps_nonvote_mean=200.0), base)
        after = anomaly.detect(snap(tps_nonvote_mean=200.0),
                               base + [history.record_from_snapshot(snap(tps_nonvote_mean=200.0))])
        self.assertNotEqual(before["anomalies"], after["anomalies"])

    def test_window_bounds_the_baseline(self):
        report = anomaly.detect(snap(), self.baseline(50), window=10)
        self.assertEqual(report["baseline"]["records"], 10)

    def test_findings_are_sorted_worst_first(self):
        report = anomaly.detect(
            snap(health="behind", superminority_count=12, delinquent_stake_pct=7.0), [])
        levels = [a["severity"] for a in report["anomalies"]]
        self.assertEqual(levels, sorted(levels, key=lambda s: anomaly.SEVERITY_RANK[s]))
        self.assertEqual(levels[0], "critical")

    def test_report_is_json_serialisable(self):
        report = anomaly.detect(snap(health="behind"), self.baseline(8))
        self.assertIsInstance(json.loads(json.dumps(report)), dict)


if __name__ == "__main__":
    unittest.main()
