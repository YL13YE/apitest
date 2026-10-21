import json
import math
import unittest
import time
from unittest.mock import patch

import httpx

from apitest.evaluation import build_overview, scenario_metadata, wilson
from apitest.runner import RunOptions
from apitest.providers import Target

NOW = 2000000000.0


def row(index=0, **changes):
    result = {"target": "relay@low", "model": "gpt-model", "provider": "openai", "effort": "low",
              "index": index, "attempt": 1, "started_at": NOW - 7200 + index * 60, "ok": True,
              "text_chars": 300, "stream": True, "ttft_text": .8 + index % 2 * .1,
              "tps": 60, "tps_eligible": True, "single_chunk": False, "chars_per_second": 80,
              "max_gap": .15, "stall_count": 0, "output_tokens": 256, "cost": .001, "budget_charge": .001,
              "gen_time": 1.0,
              "total": 4.2,
              "currency": "USD", "cost_uncertain": False, "text": "private response must not be published",
              "error": "provider may echo a private key"}
    result.update(changes)
    return result


def run(rows, ident="run-1", **meta):
    options = {"preset": "medium", "prompt_chars": 30, "system": False, "max_tokens": 512,
               "stream": True, "concurrency": 1, "cache_mode": "cold", "order": "interleaved"}
    options.update(meta)
    return {"id": ident, "meta": options, "results": rows}


class EvaluationTests(unittest.TestCase):
    def test_wilson_does_not_call_one_success_reliable(self):
        self.assertAlmostEqual(wilson(1, 1)[0], .2065493, places=6)
        self.assertGreater(wilson(100, 100)[0], .95)
        self.assertIsNone(wilson(0, 0))

    def test_one_fast_sample_remains_insufficient(self):
        view = build_overview([run([row()])], now=NOW)
        v = view["variants"][0]
        self.assertEqual(v["assessment"]["overall"]["label"], "样本不足")
        self.assertEqual(v["assessment"]["stability"]["label"], "稳定性待观察")
        self.assertEqual(v["metrics"]["ttfa_p50"], .8)
        self.assertIsNone(v["metrics"]["ttfa_p95"])

    def test_stable_label_needs_enough_time_and_samples(self):
        rows = [row(i) for i in range(100)]
        view = build_overview([run(rows)], now=NOW)
        v = view["variants"][0]
        self.assertEqual(v["assessment"]["overall"]["label"], "好用")
        self.assertEqual(v["assessment"]["stability"]["label"], "稳")
        short = [row(i, started_at=NOW - 200 + i) for i in range(100)]
        v = build_overview([run(short)], now=NOW)["variants"][0]
        self.assertNotEqual(v["assessment"]["stability"]["label"], "稳")

    def test_failures_and_truncation_stay_in_denominator(self):
        rows = [row(i) for i in range(10)]
        rows[0].update(ok=False, status_code=503)
        rows[1].update(truncated=True, stop_reason="length")
        v = build_overview([run(rows)], now=NOW)["variants"][0]
        self.assertEqual(v["metrics"]["complete"], 8)
        self.assertEqual(v["metrics"]["good_rate"], .8)
        self.assertEqual(v["assessment"]["overall"]["label"], "不稳定")
        self.assertEqual(v["errors"], {"HTTP 503": 1})

    def test_missing_stream_measurements_cannot_be_smooth(self):
        rows = [row(i, single_chunk=True, tps_eligible=False, chars_per_second=None) for i in range(8)]
        v = build_overview([run(rows)], now=NOW)["variants"][0]
        self.assertIsNone(v["metrics"]["smooth_rate"])
        self.assertIsNone(v["metrics"]["tps_p50"])
        self.assertEqual(v["metrics"]["unmeasured"], 8)
        self.assertEqual(v["assessment"]["overall"]["label"], "测量不全")

    def test_nonstream_keeps_total_latency_without_inventing_first_token(self):
        rows = [row(i, stream=False, ttft_text=0, tps_eligible=False, single_chunk=True, total=3+i) for i in range(5)]
        v = build_overview([run(rows, stream=False)], now=NOW)["variants"][0]
        self.assertEqual(v["metrics"]["total_p50"], 5)
        self.assertIsNone(v["metrics"]["ttfa_p50"])
        self.assertIsNone(v["metrics"]["smooth_rate"])

    def test_buffered_burst_is_not_called_fast_or_smooth(self):
        rows = [row(i, gen_time=.003, tps=9000, max_gap=0) for i in range(8)]
        v = build_overview([run(rows)], now=NOW)["variants"][0]
        self.assertIsNone(v["metrics"]["tps_p50"])
        self.assertIsNone(v["metrics"]["smooth_rate"])
        self.assertEqual(v["assessment"]["overall"]["label"], "测量不全")
        self.assertTrue(any("50ms" in note for note in v["notes"]))

    def test_slow_known_measurement_is_bad_even_with_missing_speed(self):
        rows = [row(i, ttft_text=20, tps_eligible=False, chars_per_second=None, single_chunk=True) for i in range(8)]
        v = build_overview([run(rows)], now=NOW)["variants"][0]
        self.assertEqual(v["metrics"]["bad"], 8)
        self.assertEqual(v["assessment"]["overall"]["label"], "偏慢 / 卡顿")

    def test_scenarios_are_never_pooled(self):
        old = run([row(0)], "old", max_tokens=64, preset="short")
        new = run([row(1)], "new", max_tokens=2048, preset="long")
        view = build_overview([old, new], now=NOW)
        self.assertEqual(len(view["cohorts"]), 2)
        self.assertEqual(view["cohort"], "all")
        self.assertEqual(view["summary"]["requests"], 2)
        self.assertEqual(len(view["variants"]), 2)
        self.assertTrue(all(v["metrics"]["n"] == 1 for v in view["variants"]))
        selected=build_overview([old,new],now=NOW,cohort=view["cohorts"][0]["id"])
        self.assertEqual(selected["summary"]["requests"], 1)

    def test_custom_unsigned_prompts_are_kept_separate(self):
        a = run([row(0)], "a", preset=None)
        b = run([row(1)], "b", preset=None)
        self.assertEqual(len(build_overview([a, b], now=NOW)["cohorts"]), 2)

    def test_prompt_fingerprint_changes_without_exposing_text(self):
        a = scenario_metadata(RunOptions("sensitive prompt", max_tokens=512), None)
        b = scenario_metadata(RunOptions("different prompt", max_tokens=512), None)
        self.assertNotEqual(a["scenario_key"], b["scenario_key"])
        self.assertNotIn("sensitive prompt", json.dumps(a))

    def test_advanced_request_settings_are_not_pooled(self):
        opts = RunOptions("same prompt")
        a = Target("a@low", "openai", "https://example.test", "", "gpt-model", extra_body={"temperature": .2}, effort="low")
        b = Target("b@low", "openai", "https://example.test", "", "gpt-model", extra_body={"temperature": .8}, effort="low")
        meta = scenario_metadata(opts, "medium", [a, b])
        view = build_overview([run([row(0, target=a.name), row(1, target=b.name)], **meta)], now=NOW)
        self.assertEqual(len(view["cohorts"]), 2)
        self.assertEqual(view["summary"]["requests"], 2)
        self.assertEqual(len(view["variants"]), 2)

    def test_matching_builtin_preset_has_one_condition_across_old_and_new_records(self):
        from apitest.prompts import PRESETS
        prompt=PRESETS["short"]["prompt"]
        old=run([row(0)],"old",preset="short",prompt_chars=len(prompt),max_tokens=64)
        new=run([row(1)],"new",**scenario_metadata(RunOptions(prompt,max_tokens=64),"short"))
        view=build_overview([old,new],now=NOW)
        self.assertEqual(len(view["cohorts"]),1)
        self.assertEqual(len(view["variants"]),1)
        self.assertEqual(view["variants"][0]["metrics"]["n"],2)

    def test_unverified_fingerprints_are_not_merged_and_labels_are_distinct(self):
        a=run([row(0)],"a",scenario_key="custom-a")
        b=run([row(1)],"b",scenario_key="custom-b")
        view=build_overview([a,b],now=NOW)
        self.assertEqual(len(view["cohorts"]),2)
        self.assertEqual(len({c["label"] for c in view["cohorts"]}),2)

    def test_different_efforts_are_not_averaged(self):
        rows = [row(0), row(1, target="relay@high", effort="high", ttft_text=20)]
        v = build_overview([run(rows)], now=NOW)["variants"]
        self.assertEqual(len(v), 2)
        self.assertEqual(sorted(x["metrics"]["ttfa_p50"] for x in v), [.8, 20])

    def test_showcase_contains_presets_and_unspecified_effort(self):
        rows = [row(i, target="relay@" + effort, effort=effort) for i, effort in enumerate(["default", "none", "low", "medium", "high", "xhigh", "max"])]
        view = build_overview([run(rows)], now=NOW)
        self.assertEqual({v["effort"] for v in view["variants"]}, {None, "low", "medium", "high", "xhigh"})
        self.assertEqual(view["summary"]["requests"], 5)

    def test_unknown_effort_is_not_relabelled_as_a_preset(self):
        sample = row(target="relay")
        sample.pop("effort")
        view = build_overview([run([sample])], now=NOW)
        self.assertEqual(len(view["variants"]), 1)
        self.assertIsNone(view["variants"][0]["effort"])
        self.assertEqual(view["variants"][0]["endpoint"], "relay")
        self.assertEqual(view["summary"]["requests"], 1)

    def test_default_effort_is_separate_from_explicit_low(self):
        rows = [row(0, target="relay@default", effort="default", ttft_text=10), row(1)]
        variants = build_overview([run(rows)], now=NOW)["variants"]
        self.assertEqual(len(variants), 2)
        self.assertEqual(len({v["id"] for v in variants}), 2)
        self.assertEqual({v["endpoint"] for v in variants}, {"relay"})
        by_effort = {v["effort"]: v for v in variants}
        self.assertEqual(by_effort[None]["metrics"]["ttfa_p50"], 10)
        self.assertEqual(by_effort["low"]["metrics"]["ttfa_p50"], .9)
        self.assertTrue(all(v["metrics"]["n"] == 1 for v in variants))

    def test_legacy_explicit_preset_suffix_is_preserved(self):
        sample = row(target="relay@xhigh")
        sample.pop("effort")
        view = build_overview([run([sample])], now=NOW)
        self.assertEqual(view["variants"][0]["effort"], "xhigh")

    def test_unknown_cost_stays_unknown_and_free_model_stays_zero(self):
        v = build_overview([run([row(cost=None, budget_charge=0, cost_unknown_reason="price_missing")])], now=NOW)["variants"][0]
        self.assertIsNone(v["cost"])
        v = build_overview([run([row(cost=0, budget_charge=0)])], now=NOW)["variants"][0]
        self.assertEqual(v["cost"], 0)

    def test_private_response_and_error_text_are_not_exported(self):
        text = json.dumps(build_overview([run([row(ok=False, status_code=503)])], now=NOW))
        self.assertNotIn("private response", text)
        self.assertNotIn("private key", text)
        self.assertNotIn('"text":', text)

    def test_warmups_excluded_from_performance_but_in_cost(self):
        v = build_overview([run([row(0), row(1, warmup=True)])], now=NOW)["variants"][0]
        self.assertEqual(v["metrics"]["n"], 1)
        self.assertEqual(v["charged_attempts"], 2)
        self.assertAlmostEqual(v["cost"], .002)

    def test_window_filter_and_empty_data(self):
        view = build_overview([run([row(started_at=NOW - 7200)])], now=NOW, hours=1)
        self.assertEqual(view["variants"], [])
        self.assertIsNone(view["cohort"])
        self.assertEqual(build_overview([], now=NOW)["summary"]["requests"], 0)

    def test_invalid_thresholds_and_nonfinite_metrics(self):
        with self.assertRaises(ValueError):
            build_overview([], thresholds={"ttfa": "nan"})
        view = build_overview([run([row(ttft_text=math.nan, tps=math.inf)])], now=NOW)
        json.dumps(view, allow_nan=False)
        self.assertIsNone(view["variants"][0]["metrics"]["ttfa_p50"])


class OverviewAPITests(unittest.IsolatedAsyncioTestCase):
    async def test_overview_is_authenticated_read_only_and_sanitized(self):
        from apitest import web
        saved = run([row(started_at=time.time() - 10, ok=False, status_code=503)])
        with patch.object(web, "load_runs", return_value=[saved]), patch.object(web, "RUNS", {}):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web.app), base_url="http://127.0.0.1") as client:
                self.assertEqual((await client.get("/api/overview")).status_code, 403)
                await client.get("/")
                response = await client.get("/api/overview")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["summary"]["requests"], 1)
                self.assertNotIn("private response", response.text)
                self.assertNotIn("private key", response.text)
                self.assertEqual((await client.get("/api/overview?ttfa=nan")).status_code, 400)
                self.assertEqual((await client.get("/api/overview?profile=unknown")).status_code, 400)


if __name__ == "__main__":
    unittest.main()
