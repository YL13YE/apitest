import argparse
import json
import tempfile
import re
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
import yaml

from apitest.budget import RunControl
from apitest.cli import cmd_import_prices
from apitest.config import endpoint_targets, load_config
from apitest.pricing import apply_price_catalogs, parse_price_table, usage_cost
from apitest.providers import Target
from apitest.runner import RunOptions, run_all


def table(**changes):
    return {"version": 1, "models": [{"modelId": "model", "displayName": "Model",
        "inputCostPerMillion": "2", "outputCostPerMillion": "10",
        "cacheReadCostPerMillion": "0.2", "cacheCreationCostPerMillion": "2.5", **changes}]}


def target(provider="openai", **changes):
    return Target("relay", provider, "https://example.test/v1", "mock-key", "model",
                  price_in=2, price_out=10, price_cache_read=.2, price_cache_write=2.5, **changes)


class PricingTests(unittest.TestCase):
    def test_multiplier_applies_to_every_rate_without_modifying_the_price_table(self):
        ep={"base_url":"https://example.test/v1","models":["model"],
            "price_defaults":parse_price_table(table()),"price_multiplier":.16,"efforts":["low","high"]}
        for t in endpoint_targets("relay",ep):
            self.assertEqual(t.price_in,2)
            self.assertEqual(t.price_multiplier,.16)
            self.assertAlmostEqual(usage_cost(t,1000,50,600,100),.00147*.16)
        self.assertEqual(ep["price_defaults"]["model"]["input"],2)
        ep["price"]={"model":[3,15]}
        self.assertAlmostEqual(usage_cost(endpoint_targets("relay",ep)[0],1000,50),.00375*.16)

    def test_default_zero_and_invalid_multipliers(self):
        self.assertAlmostEqual(usage_cost(target(),1000,50),.0025)
        self.assertEqual(usage_cost(target(price_multiplier=0),1000,50),0)
        self.assertEqual(target(price_multiplier="0.16").price_multiplier,.16)
        for value in (-1,"nan","inf",True,None,"",{}):
            with self.subTest(value=value),self.assertRaises(ValueError):
                target(price_multiplier=value)
        unknown=Target("relay","openai","https://example.test","","m",price_multiplier=0)
        self.assertIsNone(usage_cost(unknown,1000,50))

    def test_plans_and_reservations_use_the_same_multiplier(self):
        from apitest.planning import monitor_estimate
        opts=RunOptions("p",max_tokens=64,duration=3600,sample_interval=3600)
        standard,discount=target(),target(price_multiplier=.16)
        before=monitor_estimate([standard],opts,2000000000)
        after=monitor_estimate([discount],opts,2000000000)
        self.assertAlmostEqual(after["cost"],before["cost"]*.16)
        self.assertAlmostEqual(RunControl([discount],opts).reservations[discount.label],
                               RunControl([standard],opts).reservations[standard.label]*.16)
        with self.assertRaises(ValueError):
            RunControl([target(price_multiplier=1e308)],opts)

    def test_run_metadata_snapshots_rates_and_multiplier(self):
        from apitest.evaluation import scenario_metadata
        t=target(price_multiplier=.16)
        snapshot=scenario_metadata(RunOptions("p"),"short",[t])
        t.price_multiplier=1
        self.assertEqual(snapshot["pricing"][t.label]["multiplier"],.16)
        self.assertEqual(snapshot["pricing"][t.label]["input"],2)

    def test_exact_ids_and_zero_prices(self):
        prices = parse_price_table(table(inputCostPerMillion="0"))
        self.assertEqual(prices["model"]["input"], 0)
        ep = {"base_url": "https://example.test/v1", "models": ["model", "model-alias"], "price": prices}
        known, alias = endpoint_targets("relay", ep)
        self.assertEqual(known.price_cache_write, 2.5)
        self.assertIsNone(alias.price_in)

    def test_invalid_prices_and_duplicates_fail_before_import(self):
        for value in ("nan", "inf", -1, True, None, ""):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_price_table(table(inputCostPerMillion=value))
        duplicate = table()
        duplicate["models"] *= 2
        with self.assertRaises(ValueError):
            parse_price_table(duplicate)
        with self.assertRaises(ValueError):
            parse_price_table({"version": 1, "models": []})

    def test_import_preserves_credentials_and_unrelated_settings(self):
        with tempfile.TemporaryDirectory() as d:
            config, source = Path(d)/"config.yaml", Path(d)/"prices.json"
            original = {"defaults": {"rounds": 3}, "endpoints": {"relay": {
                "base_url": "https://example.test/v1", "api_key": "${TEST_KEY}",
                "models": ["model"], "efforts": ["low", "high"], "price": {"other": [1, 2]}}}}
            config.write_text(yaml.safe_dump(original))
            source.write_text(json.dumps(table()))
            args = argparse.Namespace(config=str(config), file=str(source), endpoint=["relay"], currency="USD")
            cmd_import_prices(args, {})
            saved = yaml.safe_load(config.read_text())
            self.assertEqual(saved["price_catalogs"]["relay"]["price"]["model"]["cache_read"], .2)
            self.assertEqual(load_config(config)["endpoints"]["relay"]["price_defaults"]["model"]["cache_read"], .2)
            self.assertEqual(saved["endpoints"]["relay"]["price"]["other"], [1, 2])
            for key in ("api_key", "base_url", "models", "efforts"):
                self.assertEqual(saved["endpoints"]["relay"][key], original["endpoints"]["relay"][key])
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)
            before = config.read_bytes()
            args.currency = "CNY"
            with self.assertRaises(ValueError):
                cmd_import_prices(args, {})
            self.assertEqual(config.read_bytes(), before)

    def test_catalog_survives_stale_endpoint_values_and_respects_overrides(self):
        catalog = {"relay": {"base_url": "https://example.test/v1", "provider": "", "currency": "USD", "price": parse_price_table(table())}}
        ep = {"base_url": "https://example.test/v1", "models": ["model"]}
        merged = apply_price_catalogs({"relay": ep}, catalog)
        self.assertEqual(endpoint_targets("relay", merged["relay"])[0].price_in, 2)
        self.assertNotIn("price_defaults", ep)
        ep["price"] = {"model": [3, 15]}
        merged = apply_price_catalogs({"relay": ep}, catalog)
        self.assertEqual(endpoint_targets("relay", merged["relay"])[0].price_in, 3)
        ep["currency"] = "CNY"
        self.assertNotIn("price_defaults", apply_price_catalogs({"relay": ep}, catalog)["relay"])
        ep["currency"] = "USD"
        ep["base_url"] = "https://different.test/v1"
        self.assertNotIn("price_defaults", apply_price_catalogs({"relay": ep}, catalog)["relay"])

    def test_request_uses_saved_catalog_even_from_old_browser_state(self):
        from apitest import web
        ep = {"base_url": "https://example.test/v1", "models": ["model"], "api_key": "mock-key"}
        raw = {"endpoints": {"relay": ep}, "price_catalogs": {"relay": {
            "base_url": ep["base_url"], "provider": "", "currency": "USD", "price": parse_price_table(table())}}}
        with patch.object(web, "_raw_config", return_value=raw):
            result = web._targets_from_request({"endpoints": {"relay": ep}, "targets": [{"endpoint": "relay", "model": "model"}]})
        self.assertEqual(result[0].price_in, 2)
        self.assertEqual(result[0].price_cache_write, 2.5)

    def test_cache_rates_avoid_double_counting_and_include_reasoning(self):
        self.assertAlmostEqual(usage_cost(target(), 1000, 50, 600, 100), .00147)
        self.assertIsNone(usage_cost(target(), 100, 50, 600))
        self.assertIsNone(usage_cost(target(), -1, 50))
        plain = Target("relay", "openai", "https://example.test", "", "m", price_in=2, price_out=10)
        self.assertIsNone(usage_cost(plain, 1000, 50, 600))
        self.assertAlmostEqual(usage_cost(plain, 1000, 50), .0025)


class EventStream(httpx.AsyncByteStream):
    def __init__(self, events):
        self.events = events

    async def __aiter__(self):
        for event in self.events:
            yield ("data: " + json.dumps(event) + "\n\n").encode()


class PricingProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_mock_request_accounts_for_the_multiplier_and_records_it(self):
        body={"choices":[{"message":{"content":"hello"},"finish_reason":"stop"}],
              "usage":{"prompt_tokens":1000,"completion_tokens":50}}
        opts=RunOptions("p",rounds=1,stream=False,budget=1)
        with patch("apitest.runner.make_client",lambda _:httpx.AsyncClient(transport=httpx.MockTransport(lambda req:httpx.Response(200,json=body)))):
            result=(await run_all([target(price_multiplier=.16)],opts))[0]
        self.assertAlmostEqual(result.cost,.0025*.16)
        self.assertAlmostEqual(result.budget_charge,result.cost)
        self.assertEqual(result.to_dict()["price_multiplier"],.16)
        self.assertAlmostEqual(opts.control.snapshot()["reported_cost"],result.cost)

    async def test_configuration_roundtrip_validates_and_preserves_multiplier(self):
        from apitest import web
        with tempfile.TemporaryDirectory() as directory,patch.object(web,"CONFIG_PATH",Path(directory)/"config.yaml"):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web.app),base_url="http://127.0.0.1") as client:
                page=await client.get("/")
                token=re.search(r'name="apitest-token" content="([^"]+)"',page.text)[1]
                ep={"base_url":"https://example.test","models":["model"],"price_multiplier":.16}
                headers={"X-APITest-Token":token}
                response=await client.post("/api/config",json={"endpoints":{"relay":ep}},headers=headers)
                self.assertEqual(response.status_code,200)
                loaded=(await client.get("/api/config")).json()
                self.assertTrue(loaded["supports_price_multiplier"])
                self.assertEqual(loaded["endpoints"]["relay"]["price_multiplier"],.16)
                before=web.CONFIG_PATH.read_bytes()
                for value in (-1,True,"nan",None):
                    ep["price_multiplier"]=value
                    response=await client.post("/api/config",json={"endpoints":{"relay":ep}},headers=headers)
                    self.assertEqual(response.status_code,400)
                    self.assertEqual(web.CONFIG_PATH.read_bytes(),before)

    async def test_reported_extra_tokens_increase_future_reservations(self):
        from apitest.runner import RequestResult
        t=target();opts=RunOptions('p',budget=1,max_tokens=64)
        guard=RunControl([t],opts);original=guard.reservations[t.label]
        ticket=await guard.reserve(t)
        await guard.settle(ticket,RequestResult(t.name,t.model,t.provider,0,ok=True,input_tokens=10000,output_tokens=2000,cost=.04))
        self.assertGreater(guard.reservations[t.label],original)
        self.assertAlmostEqual(guard.reservations[t.label],.045)
        self.assertAlmostEqual(guard.spent,.04)

    async def test_all_protocols_use_reported_cache_tokens(self):
        cases = {
            "openai": {"choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}], "usage": {
                "prompt_tokens": 1000, "completion_tokens": 50, "prompt_tokens_details": {"cached_tokens": 600, "cache_write_tokens": 100}, "completion_tokens_details": {"reasoning_tokens": 20}}},
            "responses": {"output": [{"content": [{"type": "output_text", "text": "hello"}]}], "usage": {
                "input_tokens": 1000, "output_tokens": 50, "input_tokens_details": {"cached_tokens": 600, "cache_write_tokens": 100}, "output_tokens_details": {"reasoning_tokens": 20}}},
            "anthropic": {"content": [{"type": "text", "text": "hello"}], "stop_reason": "end_turn", "usage": {
                "input_tokens": 300, "output_tokens": 50, "cache_read_input_tokens": 600, "cache_creation_input_tokens": 100}},
            "gemini": {"candidates": [{"content": {"parts": [{"text": "hello"}]}, "finishReason": "STOP"}], "usageMetadata": {
                "promptTokenCount": 1000, "candidatesTokenCount": 30, "thoughtsTokenCount": 20, "cachedContentTokenCount": 600}},
        }
        for provider, body in cases.items():
            with self.subTest(provider=provider):
                def factory(_):
                    return httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=body)))
                with patch("apitest.runner.make_client", factory):
                    result = (await run_all([target(provider)], RunOptions("p", rounds=1, stream=False)))[0]
                self.assertTrue(result.ok)
                self.assertEqual(result.input_tokens, 1000)
                self.assertEqual(result.cache_read_tokens, 600)
                self.assertEqual(result.output_tokens, 50)
                self.assertFalse(result.cost_uncertain)
                self.assertAlmostEqual(result.cost, .00142 if provider == "gemini" else .00147)

    async def test_anthropic_stream_preserves_start_usage(self):
        events = [{"type": "message_start", "message": {"usage": {"input_tokens": 300, "cache_read_input_tokens": 600, "cache_creation_input_tokens": 100}}},
                  {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hello"}},
                  {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 50}}]
        def factory(_):
            return httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, stream=EventStream(events))))
        with patch("apitest.runner.make_client", factory):
            result = (await run_all([target("anthropic")], RunOptions("p", rounds=1)))[0]
        self.assertEqual(result.input_tokens, 1000)
        self.assertEqual(result.cache_write_tokens, 100)
        self.assertAlmostEqual(result.cost, .00147)

    async def test_cache_writes_are_reserved_before_requests(self):
        priced = target()
        baseline = Target("relay", "openai", "https://example.test", "", "model", price_in=2, price_out=10)
        opts = RunOptions("p", max_tokens=10)
        conservative = RunControl([priced], opts).reservations[priced.label]
        ordinary = RunControl([baseline], opts).reservations[baseline.label]
        self.assertGreater(conservative, ordinary)
        guard = RunControl([priced], RunOptions("p", max_tokens=10, budget=(conservative + ordinary)/2))
        self.assertIsNone(await guard.reserve(priced))

    async def test_chat_responses_and_gemini_stream_cache_usage(self):
        cases = {
            "openai": [{"choices": [{"delta": {"content": "hello"}}]}, {"choices": [], "usage": {
                "prompt_tokens": 1000, "completion_tokens": 50, "prompt_tokens_details": {"cached_tokens": 600, "cache_write_tokens": 100}}}],
            "responses": [{"type": "response.output_text.delta", "delta": "hello"}, {"type": "response.completed", "response": {"usage": {
                "input_tokens": 1000, "output_tokens": 50, "input_tokens_details": {"cached_tokens": 600, "cache_write_tokens": 100}}}}],
            "gemini": [{"candidates": [{"content": {"parts": [{"text": "hello"}]}}], "usageMetadata": {
                "promptTokenCount": 1000, "candidatesTokenCount": 50, "cachedContentTokenCount": 600}}],
        }
        for provider, events in cases.items():
            with self.subTest(provider=provider):
                def factory(_):
                    return httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, stream=EventStream(events))))
                with patch("apitest.runner.make_client", factory):
                    result = (await run_all([target(provider)], RunOptions("p", rounds=1)))[0]
                self.assertTrue(result.ok)
                self.assertEqual(result.cache_read_tokens, 600)
                self.assertAlmostEqual(result.cost, .00142 if provider == "gemini" else .00147)


if __name__ == "__main__":
    unittest.main()
