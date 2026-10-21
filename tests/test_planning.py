from datetime import datetime
import re
import unittest
from unittest.mock import patch

import httpx

from apitest import web
from apitest.planning import monitor_estimate, observed_usage_bounds
from apitest.evaluation import scenario_metadata
from apitest.providers import Target
from apitest.runner import RunOptions
from apitest.scheduling import TIMEZONE, sample_round_bound


START = datetime(2026, 10, 20, 20, 30, tzinfo=TIMEZONE).timestamp()


def targets(count=24, output_price=2):
    return [Target("relay", "openai", "https://example.test/v1", "private-estimate-key", "model-" + str(i),
                   price_in=0, price_out=output_price) for i in range(count)]


def options(**changes):
    return RunOptions("private-estimate-prompt", max_tokens=512, duration=7 * 86400,
        sample_interval=3600, monitor_hours=list(range(8, 24)), **changes)


class PlanningTests(unittest.TestCase):
    def test_usage_calibration_accounts_for_large_reported_input_and_output(self):
        ts=targets(1);ts[0].price_in=1
        opts=options()
        runs=[{"id":"history","meta":scenario_metadata(opts,None,ts),"results":[{
            "target":"relay","model":"model-0","provider":"openai","input_tokens":9300,"output_tokens":2048,"tokens_estimated":False}]}]
        bounds,count=observed_usage_bounds(runs,ts,opts,None)
        self.assertEqual(count,1)
        self.assertEqual(bounds[ts[0].label],{"input":9300,"output":2048})
        calibrated=monitor_estimate(ts,opts,START,usage_bounds=bounds,observed_samples=count)
        self.assertEqual(calibrated["method"],"observed_usage")
        self.assertAlmostEqual(calibrated["cost"],113*(9300+2048*2)/1e6)
        other=RunOptions("different prompt",max_tokens=512,duration=7*86400,sample_interval=3600)
        self.assertEqual(observed_usage_bounds(runs,ts,other,None),({},0))

    def test_usage_calibration_uses_same_model_across_reasoning_efforts(self):
        ts=[Target("relay@"+effort,"openai","https://example.test/v1","","model",price_in=1,price_out=2,effort=effort)for effort in ("low","high")]
        opts=options()
        runs=[{"id":"history","meta":scenario_metadata(opts,None,ts),"results":[{
            "target":"relay@high","model":"model","provider":"openai","effort":"high","input_tokens":9000,"output_tokens":200}]}]
        bounds,count=observed_usage_bounds(runs,ts,opts,None)
        self.assertEqual(count,1)
        self.assertEqual(bounds[ts[0].label]["input"],9000)
        self.assertEqual(bounds[ts[1].label]["input"],9000)
    def test_24_combinations_and_2712_requests_use_output_cap_prices(self):
        preview = monitor_estimate(targets(), options(), START)
        self.assertEqual(preview["rounds"], 113)
        self.assertEqual(preview["requests"], 2712)
        self.assertAlmostEqual(preview["cost"], 2712 * 512 * 2 / 1e6)
        self.assertEqual(preview["currency"], "USD")

    def test_full_plan_is_not_clipped_by_budget(self):
        preview = monitor_estimate(targets(), options(budget=.001, hourly_budget=.001), START)
        self.assertAlmostEqual(preview["cost"], 2.777088)
        self.assertEqual(preview["requests"], 2712)

    def test_warmups_and_retries_are_counted_separately(self):
        preview = monitor_estimate(targets(), options(warmup=2, retries=1), START)
        self.assertEqual(preview["requests"], (113 * 2 + 2) * 24)
        self.assertAlmostEqual(preview["cost"], (113 * 2 + 2) * 24 * 512 * 2 / 1e6)

    def test_missing_prices_are_unknown_and_free_prices_are_zero(self):
        preview = monitor_estimate(targets(output_price=None), options(), START)
        self.assertIsNone(preview["cost"])
        self.assertEqual(preview["missing_prices"], 24)
        self.assertEqual(monitor_estimate(targets(output_price=0), options(), START)["cost"], 0)

    def test_higher_cache_write_price_is_reserved_without_assuming_hits(self):
        basic = targets(1)
        basic[0].price_in = 1
        basic[0].price_out = 0
        ordinary = monitor_estimate(basic, options(), START)["cost"]
        basic[0].price_cache_read = .1
        basic[0].price_cache_write = 2
        self.assertAlmostEqual(monitor_estimate(basic, options(), START)["cost"], ordinary * 2)

    def test_schedule_bound_includes_partial_windows_and_excludes_idle_time(self):
        aligned = datetime(2026, 10, 20, 8, tzinfo=TIMEZONE).timestamp()
        self.assertEqual(sample_round_bound(aligned, 7 * 86400, 3600, list(range(8, 24))), 112)
        self.assertEqual(sample_round_bound(START, 7 * 86400, 3600, list(range(24))), 168)
        self.assertEqual(sample_round_bound(aligned, 3600, 60, [18]), 0)
        with self.assertRaises(ValueError):
            sample_round_bound(float("inf"), 3600, 60, [18])


class EstimateAPITests(unittest.IsolatedAsyncioTestCase):
    async def test_preview_needs_no_budget_and_never_starts_or_persists_a_run(self):
        with patch.object(web, "RUNS", {}), patch.object(web, "_raw_config", return_value={"endpoints": {}}), \
             patch.object(web, "run_all") as run, patch.object(web, "save_run") as save:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web.app), base_url="http://127.0.0.1") as client:
                body = {"endpoints": {"relay": {"base_url": "https://example.test/v1", "api_key": "private-estimate-key",
                    "models": ["model"], "efforts": ["low", "high"], "price": {"model": [0, 2]}}},
                    "targets": [{"endpoint": "relay", "model": "model"}], "start_at": START,
                    "options": {"mode": "monitor", "days": 7, "sample_minutes": 60, "monitor_hours": list(range(8, 24)),
                        "prompt": "private-estimate-prompt", "max_tokens": 512}}
                self.assertEqual((await client.post("/api/monitor/estimate", json=body)).status_code, 403)
                index = await client.get("/")
                token = re.search(r'name="apitest-token" content="([^"]+)"', index.text).group(1)
                headers = {"X-APITest-Token": token}
                response = await client.post("/api/monitor/estimate", json=body, headers=headers)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["requests"], 226)
                self.assertAlmostEqual(response.json()["cost"], 226 * 512 * 2 / 1e6)
                self.assertNotIn("private-estimate-key", response.text)
                self.assertNotIn("private-estimate-prompt", response.text)
                self.assertEqual(web.RUNS, {})
                run.assert_not_called()
                save.assert_not_called()
                self.assertEqual((await client.post("/api/run", json=body, headers=headers)).status_code, 400)


if __name__ == "__main__":
    unittest.main()
