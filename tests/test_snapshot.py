import json
import unittest
from unittest import mock

from solpulse import cli, snapshot as snap_mod
from solpulse.rpc import RpcClient, RpcError

from .fakes import ALL_RPC, rpc_transport


def sources_from(rpc_stub=None, price=None, tvl=None):
    client = RpcClient(transport=rpc_transport(rpc_stub or ALL_RPC))
    mapping = snap_mod.default_sources(client)
    if price is not None:
        mapping["price"] = price
    if tvl is not None:
        mapping["tvl"] = tvl
    return mapping


class SnapshotTests(unittest.TestCase):
    def test_all_sources_ok(self):
        snap = snap_mod.collect_snapshot(
            sources_from(price=lambda: {"sol_usd": 88.0}, tvl=lambda: {"tvl_usd": 1.0})
        )
        self.assertTrue(snap["summary"]["complete"])
        self.assertEqual(snap["summary"]["sources_ok"], 5)
        self.assertEqual(snap["summary"]["failed_sources"], [])
        self.assertEqual(snap["schema"], "solpulse/snapshot/v1")

    def test_one_failing_source_does_not_sink_the_others(self):
        def boom():
            raise RuntimeError("coingecko rate limit")

        snap = snap_mod.collect_snapshot(sources_from(price=boom, tvl=lambda: {"tvl_usd": 1.0}))
        self.assertFalse(snap["summary"]["complete"])
        self.assertEqual(snap["summary"]["failed_sources"], ["price"])
        self.assertEqual(snap["sources"]["price"]["status"], "error")
        self.assertIn("coingecko rate limit", snap["sources"]["price"]["error"])
        # The on-chain sources still carry real data.
        self.assertEqual(snap["sources"]["network"]["data"]["epoch"], 1019)

    def test_failed_source_has_no_data_key(self):
        """The bug this guards: an error must never masquerade as a value."""

        def boom():
            raise RuntimeError("node down")

        snap = snap_mod.collect_snapshot(sources_from(price=boom, tvl=lambda: {}))
        self.assertNotIn("data", snap["sources"]["price"])

    def test_rpc_failure_is_isolated_per_source(self):
        stub = dict(ALL_RPC, getVoteAccounts={"error": {"code": -32004, "message": "unavailable"}})
        snap = snap_mod.collect_snapshot(
            sources_from(stub, price=lambda: {"sol_usd": 1.0}, tvl=lambda: {"tvl_usd": 1.0})
        )
        self.assertEqual(snap["sources"]["validators"]["status"], "error")
        self.assertEqual(snap["sources"]["network"]["status"], "ok")
        self.assertIn(RpcError.__name__, snap["sources"]["validators"]["error"])

    def test_snapshot_is_json_serialisable(self):
        snap = snap_mod.collect_snapshot(
            sources_from(price=lambda: {"sol_usd": 88.0}, tvl=lambda: {"tvl_usd": 1.0})
        )
        self.assertIsInstance(json.dumps(snap), str)

    def test_generated_at_is_utc_iso(self):
        snap = snap_mod.collect_snapshot({})
        self.assertRegex(snap["generated_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_elapsed_recorded_for_ok_and_error(self):
        ticks = iter([0.0, 0.5, 1.0, 1.25])
        snap = snap_mod.collect_snapshot(
            {"good": lambda: {"x": 1}, "bad": lambda: (_ for _ in ()).throw(RuntimeError("x"))},
            clock=lambda: next(ticks),
        )
        self.assertEqual(snap["sources"]["good"]["elapsed_ms"], 500.0)
        self.assertEqual(snap["sources"]["bad"]["elapsed_ms"], 250.0)


class GetterTests(unittest.TestCase):
    SNAP = {
        "sources": {
            "network": {"status": "ok", "data": {"epoch": 7, "throughput": {"tps_mean": 3500.0}, "nothing": None}},
            "price": {"status": "error", "error": "HttpError: 429"},
        }
    }

    def test_reads_nested_path(self):
        self.assertEqual(snap_mod.get(self.SNAP, "network", "throughput", "tps_mean"), 3500.0)

    def test_failed_source_yields_default(self):
        self.assertIsNone(snap_mod.get(self.SNAP, "price", "sol_usd"))
        self.assertEqual(snap_mod.get(self.SNAP, "price", "sol_usd", default="unknown"), "unknown")

    def test_missing_source_yields_default(self):
        self.assertIsNone(snap_mod.get(self.SNAP, "nope", "x"))

    def test_missing_key_yields_default(self):
        self.assertIsNone(snap_mod.get(self.SNAP, "network", "throughput", "absent"))

    def test_explicit_none_yields_default(self):
        """A None metric and a missing one both mean 'unknown' to a renderer."""
        self.assertEqual(snap_mod.get(self.SNAP, "network", "nothing", default="unknown"), "unknown")


class CliTests(unittest.TestCase):
    def snapshot_with(self, **overrides):
        base = snap_mod.collect_snapshot(
            sources_from(price=lambda: {"sol_usd": 88.0}, tvl=lambda: {"tvl_usd": 1.0})
        )
        base.update(overrides)
        return base

    def test_collect_prints_digest_and_exits_zero(self):
        snap = self.snapshot_with()
        with mock.patch.object(cli, "snapshot", return_value=snap), \
             mock.patch("sys.stdout") as out:
            code = cli.main(["collect"])
        self.assertEqual(code, 0)
        printed = "".join(c.args[0] for c in out.write.call_args_list if c.args)
        self.assertIn("Solana ecosystem snapshot", printed)
        self.assertIn("1019", printed)

    def test_degraded_snapshot_exits_nonzero(self):
        def boom():
            raise RuntimeError("down")

        snap = snap_mod.collect_snapshot(sources_from(price=boom, tvl=lambda: {"tvl_usd": 1.0}))
        with mock.patch.object(cli, "snapshot", return_value=snap), mock.patch("sys.stdout"):
            self.assertEqual(cli.main(["collect"]), 1)

    def test_unknown_metrics_render_as_unknown_not_zero(self):
        def boom():
            raise RuntimeError("down")

        snap = snap_mod.collect_snapshot(sources_from(price=boom, tvl=lambda: {"tvl_usd": 1.0}))
        text = cli._human(snap)
        self.assertIn("SOL price                    unknown", text)
        self.assertIn("Failed:  price", text)

    def test_out_writes_valid_json(self):
        import tempfile, os

        snap = self.snapshot_with()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "snap.json")
            with mock.patch.object(cli, "snapshot", return_value=snap), mock.patch("sys.stdout"):
                cli.main(["collect", "--out", path, "--json"])
            with open(path) as fh:
                written = json.load(fh)
        self.assertEqual(written["schema"], "solpulse/snapshot/v1")


if __name__ == "__main__":
    unittest.main()
