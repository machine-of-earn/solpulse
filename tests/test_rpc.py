import unittest

from solpulse.http import HttpError
from solpulse.rpc import RpcClient, RpcError

from .fakes import ALL_RPC, rpc_transport


class RpcClientTests(unittest.TestCase):
    def client(self, **kw):
        return RpcClient(transport=rpc_transport(ALL_RPC, **kw))

    def test_call_returns_result(self):
        self.assertEqual(self.client().call("getHealth"), "ok")

    def test_batch_preserves_request_order(self):
        got = self.client().batch([("getHealth", None), ("getEpochInfo", None)])
        self.assertEqual(got[0], "ok")
        self.assertEqual(got[1]["epoch"], 1019)

    def test_batch_is_one_round_trip(self):
        transport = rpc_transport(ALL_RPC)
        RpcClient(transport=transport).batch(
            [("getHealth", None), ("getEpochInfo", None), ("getSupply", [{}])]
        )
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(len(transport.calls[0]), 3)

    def test_empty_batch_makes_no_request(self):
        transport = rpc_transport(ALL_RPC)
        self.assertEqual(RpcClient(transport=transport).batch([]), [])
        self.assertEqual(transport.calls, [])

    def test_request_ids_are_unique_across_batches(self):
        transport = rpc_transport(ALL_RPC)
        client = RpcClient(transport=transport)
        client.batch([("getHealth", None), ("getHealth", None)])
        client.batch([("getHealth", None)])
        ids = [item["id"] for call in transport.calls for item in call]
        self.assertEqual(len(ids), len(set(ids)))

    def test_results_matched_by_id_not_position(self):
        """A node may answer a batch out of order; results must still line up."""

        def shuffling(endpoint, payload, timeout):
            inner = rpc_transport(ALL_RPC)(endpoint, payload, timeout)
            return list(reversed(inner))

        got = RpcClient(transport=shuffling).batch([("getHealth", None), ("getEpochInfo", None)])
        self.assertEqual(got[0], "ok")
        self.assertEqual(got[1]["epoch"], 1019)

    def test_node_error_raises(self):
        transport = rpc_transport({"getSupply": {"error": {"code": -32601, "message": "nope"}}})
        with self.assertRaises(RpcError) as ctx:
            RpcClient(transport=transport).call("getSupply")
        self.assertIn("nope", str(ctx.exception))

    def test_http_failure_becomes_rpc_error(self):
        with self.assertRaises(RpcError):
            self.client(fail_with=HttpError("connection refused")).call("getHealth")

    def test_non_list_response_rejected(self):
        with self.assertRaises(RpcError):
            RpcClient(transport=lambda *a: {"jsonrpc": "2.0", "id": 1, "result": "ok"}).call("getHealth")

    def test_short_response_rejected(self):
        with self.assertRaises(RpcError):
            RpcClient(transport=lambda *a: []).call("getHealth")

    def test_response_without_result_or_error_rejected(self):
        with self.assertRaises(RpcError):
            RpcClient(transport=lambda *a: [{"jsonrpc": "2.0", "id": 1}]).call("getHealth")


if __name__ == "__main__":
    unittest.main()
