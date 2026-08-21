import unittest
from unittest import mock

from solpulse.rpc import RpcClient
from solpulse.sources import economics, network, offchain, validators

from .fakes import ALL_RPC, PERF_SAMPLES, rpc_transport


class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.data = network.collect(RpcClient(transport=rpc_transport(ALL_RPC)))

    def test_reports_epoch_and_height(self):
        self.assertEqual(self.data["epoch"], 1019)
        self.assertEqual(self.data["block_height"], 418629925)
        self.assertEqual(self.data["health"], "ok")

    def test_epoch_progress_is_exact(self):
        # slotIndex 216000 of 432000 slots is exactly half an epoch.
        self.assertEqual(self.data["epoch_progress_pct"], 50.0)
        self.assertEqual(self.data["epoch_slots_remaining"], 216000)

    def test_latest_tps_uses_only_the_newest_sample(self):
        # 240000 tx over 60s.
        self.assertEqual(self.data["throughput"]["tps_latest"], 4000.0)
        self.assertEqual(self.data["throughput"]["tps_nonvote_latest"], 2000.0)

    def test_mean_tps_spans_the_whole_window(self):
        # (240000 + 180000) tx over 120s.
        self.assertEqual(self.data["throughput"]["tps_mean"], 3500.0)
        self.assertEqual(self.data["throughput"]["sample_window_secs"], 120)

    def test_slot_time_derived_from_samples(self):
        # 120s over 300 slots = 400ms.
        self.assertEqual(self.data["throughput"]["slot_time_ms_mean"], 400.0)

    def test_epoch_eta_uses_observed_slot_time(self):
        # 216000 slots remaining at 400ms.
        self.assertEqual(self.data["epoch_eta_seconds"], 86400)

    def test_empty_samples_raise_rather_than_report_zero(self):
        stub = dict(ALL_RPC, getRecentPerformanceSamples=[])
        with self.assertRaises(ValueError):
            network.collect(RpcClient(transport=rpc_transport(stub)))

    def test_samples_missing_period_are_skipped(self):
        stub = dict(ALL_RPC, getRecentPerformanceSamples=[{"numTransactions": 1}] + PERF_SAMPLES)
        data = network.collect(RpcClient(transport=rpc_transport(stub)))
        self.assertEqual(data["throughput"]["samples_used"], 2)


class ValidatorTests(unittest.TestCase):
    def setUp(self):
        self.data = validators.collect(RpcClient(transport=rpc_transport(ALL_RPC)))

    def test_counts_active_and_delinquent(self):
        self.assertEqual(self.data["active_count"], 4)
        self.assertEqual(self.data["delinquent_count"], 1)
        self.assertEqual(self.data["total_count"], 5)
        self.assertEqual(self.data["delinquent_pct"], 20.0)

    def test_delinquent_stake_is_measured_not_counted(self):
        # 100k delinquent SOL against 10.1M total staked.
        self.assertEqual(self.data["delinquent_stake_sol"], 100000.0)
        self.assertEqual(self.data["delinquent_stake_pct"], 0.99)

    def test_superminority_is_smallest_set_over_one_third(self):
        # 4M of 10M is 40% > 33.3%, so a single validator is the superminority.
        self.assertEqual(self.data["superminority_count"], 1)

    def test_top_validators_ranked_by_stake(self):
        top = self.data["top_validators"]
        self.assertEqual([v["vote_pubkey"] for v in top], ["v1", "v2", "v3", "v4"])
        self.assertEqual(top[0]["stake_share_pct"], 40.0)

    def test_commission_stats(self):
        self.assertEqual(self.data["zero_commission_count"], 2)
        self.assertEqual(self.data["median_commission_pct"], 5)

    def test_empty_validator_set_reports_none_not_zero(self):
        stub = dict(ALL_RPC, getVoteAccounts={"current": [], "delinquent": []})
        data = validators.collect(RpcClient(transport=rpc_transport(stub)))
        self.assertIsNone(data["delinquent_pct"])
        self.assertIsNone(data["superminority_count"])
        self.assertIsNone(data["top10_stake_share_pct"])


class EconomicsTests(unittest.TestCase):
    def test_supply_and_inflation(self):
        data = economics.collect(RpcClient(transport=rpc_transport(ALL_RPC)))
        self.assertEqual(data["circulating_supply_sol"], 583000000.0)
        self.assertEqual(data["total_supply_sol"], 632000000.0)
        self.assertEqual(data["circulating_pct"], 92.25)
        self.assertEqual(data["inflation_total_pct"], 3.688)


class OffchainTests(unittest.TestCase):
    """Parsing is tested against captured payload shapes; no network calls."""

    def price(self, payload):
        with mock.patch.object(offchain, "get_json", return_value=payload):
            return offchain.collect_price()

    def tvl(self, payload):
        with mock.patch.object(offchain, "get_json", return_value=payload):
            return offchain.collect_tvl()

    def test_price_parsed(self):
        data = self.price({"solana": {"usd": 88.07, "usd_24h_change": 3.0457, "usd_market_cap": 5.1e10}})
        self.assertEqual(data["sol_usd"], 88.07)
        self.assertEqual(data["sol_usd_24h_change_pct"], 3.05)
        self.assertEqual(data["source"], "coingecko")

    def test_price_missing_field_raises(self):
        with self.assertRaises(ValueError):
            self.price({"solana": {}})

    def test_price_change_may_be_absent(self):
        data = self.price({"solana": {"usd": 88.07}})
        self.assertEqual(data["sol_usd"], 88.07)
        self.assertIsNone(data["sol_usd_24h_change_pct"])

    def test_tvl_changes_computed_from_history(self):
        data = self.tvl([{"date": i, "tvl": 1000.0 + i} for i in range(40)])
        self.assertEqual(data["tvl_usd"], 1039.0)
        self.assertEqual(data["history_days"], 40)
        self.assertEqual(data["tvl_change_1d_pct"], round(100.0 * 1 / 1038.0, 2))
        self.assertEqual(data["tvl_change_30d_pct"], round(100.0 * 30 / 1009.0, 2))

    def test_tvl_change_is_none_when_history_too_short(self):
        data = self.tvl([{"date": 1, "tvl": 100.0}])
        self.assertIsNone(data["tvl_change_7d_pct"])
        self.assertEqual(data["tvl_usd"], 100.0)

    def test_empty_tvl_series_raises(self):
        with self.assertRaises(ValueError):
            self.tvl([])

    def test_tvl_series_without_tvl_points_raises(self):
        with self.assertRaises(ValueError):
            self.tvl([{"date": 1}])


if __name__ == "__main__":
    unittest.main()
