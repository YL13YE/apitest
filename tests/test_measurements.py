import copy
import json
import unittest
from unittest.mock import patch

import httpx

from apitest.measurements import normalize_result
from apitest.metrics import hourly_rows, normalize_saved_run, summarize
from apitest.providers import Target
from apitest.runner import RequestResult, RunOptions, run_all


def row(**changes):
    return {"target":"relay","model":"model","ok":True,"stream":True,"index":0,"attempt":1,
        "started_at":1000,"gen_time":.0024,"tps":13437.31,"tps_eligible":True,
        "input_tokens":9300,"output_tokens":32,"reasoning_tokens":0,"single_chunk":False,
        "text_chars":42,"cost":.02,**changes}


class MeasurementTests(unittest.TestCase):
    def test_13437_tok_per_second_burst_is_not_a_valid_speed(self):
        original=row();clean=normalize_result(original,{"max_tokens":64})
        self.assertIsNone(clean["tps"])
        self.assertFalse(clean["tps_eligible"])
        self.assertEqual(clean["tps_issue"],"short_window")
        self.assertEqual(original["tps"],13437.31)
        self.assertEqual(clean["cost"],.02)
        self.assertEqual(clean["input_tokens"],9300)

    def test_input_tokens_are_not_subject_to_output_limit(self):
        clean=normalize_result(row(gen_time=1,tps=32),{"max_tokens":64})
        self.assertFalse(clean["output_limit_exceeded"])
        self.assertEqual(clean["tps"],32)

    def test_reported_output_over_limit_remains_in_cost_but_not_speed(self):
        clean=normalize_result(row(gen_time=1,tps=70,output_tokens=164,reasoning_tokens=94),{"max_tokens":64})
        self.assertTrue(clean["output_limit_exceeded"])
        self.assertEqual(clean["tps_issue"],"output_limit")
        self.assertIsNone(clean["tps"])
        self.assertEqual(clean["output_tokens"],164)
        self.assertEqual(clean["cost"],.02)

    def test_valid_high_speeds_are_not_arbitrarily_capped(self):
        clean=normalize_result(row(gen_time=.5,tps=1200,output_tokens=600),{"max_tokens":1024})
        self.assertTrue(clean["tps_eligible"])
        self.assertEqual(clean["tps"],1200)

    def test_saved_summary_is_recomputed_without_changing_history(self):
        original={"meta":{"max_tokens":64},"results":[row()],"summaries":[{"target":"relay","model":"model","tps":{"n":1,"mean":13437.31}}]}
        before=copy.deepcopy(original);clean=normalize_saved_run(original)
        self.assertEqual(clean["summaries"][0]["tps"]["n"],0)
        self.assertIsNone(clean["results"][0]["tps"])
        self.assertEqual(original,before)

    def test_live_and_saved_rows_apply_the_same_speed_rule(self):
        result=RequestResult("relay","model","openai",0,ok=True,stream=True,output_tokens=32,
            requested_max_tokens=64,text_chunk_times=[1,1.0024],total=7)
        self.assertEqual(result.tps,0)
        self.assertIsNone(result.to_dict()["tps"])
        self.assertEqual(summarize([result])[0].tps.n,0)
        self.assertEqual(summarize([result])[0].tps_series,[None])

    def test_hourly_statistics_also_reject_old_burst_values(self):
        bucket=hourly_rows([row()])[0]
        self.assertIsNone(bucket["tps_p50"])

    def test_summary_only_history_still_has_no_request_details(self):
        old={"meta":{},"summaries":[{"target":"relay","model":"model","tps":{"n":1,"mean":13437}}]}
        clean=normalize_saved_run(old)
        self.assertNotIn("results",clean)
        self.assertEqual(clean["summaries"][0]["tps"]["n"],0)

    def test_invalid_durations_do_not_leak_nan_to_json(self):
        clean=normalize_result(row(gen_time=float("nan")))
        self.assertIsNone(clean["tps"])
        json.dumps(clean,allow_nan=False)


class RequestLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_gpt_request_sends_64_and_flags_reported_164(self):
        sent=[]
        def handler(request):
            sent.append(json.loads(request.content))
            return httpx.Response(200,json={"choices":[{"message":{"content":"hello"},"finish_reason":"stop"}],
                "usage":{"prompt_tokens":9300,"completion_tokens":164,"completion_tokens_details":{"reasoning_tokens":94}}})
        target=Target("relay","openai","https://example.test/v1","mock-key","gpt-6-sol",price_in=1,price_out=2)
        with patch("apitest.runner.make_client",lambda _:httpx.AsyncClient(transport=httpx.MockTransport(handler))):
            result=(await run_all([target],RunOptions("short",rounds=1,max_tokens=64,stream=False)))[0]
        self.assertEqual(sent[0]["max_completion_tokens"],64)
        self.assertEqual(result.requested_max_tokens,64)
        self.assertEqual(result.requested_limit_field,"max_completion_tokens")
        self.assertTrue(result.to_dict()["output_limit_exceeded"])
        self.assertEqual(result.output_tokens,164)
        self.assertAlmostEqual(result.cost,(9300+164*2)/1e6)


if __name__ == "__main__":unittest.main()
