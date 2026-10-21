import asyncio
import csv
import io
import json
import os
import re
import stat
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from apitest.budget import RunControl, validate_options
from apitest.config import endpoint_targets
from apitest.metrics import hourly_rows, rank, summarize
from apitest.providers import Target, build_body, parse_stream
from apitest.report import to_csv, build_payload
from apitest.runner import RunOptions, RequestResult, run_all
from apitest.security import MASK, LocalBoundary, private_write, public_endpoints, resolve_browser_endpoints, register_secrets
from apitest import web


def target(name="relay", provider="openai", **kw):
    return Target(name, provider, kw.pop("base_url", "https://example.test/v1"), "test-credential-123", "model", **kw)


class TextStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        events = [
            {"choices": [{"delta": {"reasoning_content": "thinking"}}]},
            {"choices": [{"delta": {"content": "hello "}}]},
            {"choices": [{"delta": {"content": "world"}}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 5, "completion_tokens": 12, "completion_tokens_details": {"reasoning_tokens": 10}}},
        ]
        for ev in events:
            await asyncio.sleep(.002)
            yield ("data: " + json.dumps(ev) + "\n\n").encode()
        yield b"data: [DONE]\n\n"


class SafetyTests(unittest.TestCase):
    def test_private_atomic_files_and_symlink_refusal(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "private" / "config.yaml"
            private_write(p, "secret")
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(p.parent.stat().st_mode), 0o700)
            p.chmod(0o644)
            private_write(p, "new")
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
            link = Path(d) / "link"
            link.symlink_to(p)
            with self.assertRaises(ValueError):
                private_write(link, "bad")
            self.assertEqual(p.read_text(), "new")

    def test_credentials_are_bound_to_saved_destination(self):
        saved = {"a": {"provider": "openai", "base_url": "https://api.example.test/v1", "api_key": "${SECRET_FOR_TEST}", "models": ["m"]}}
        public = public_endpoints(saved)
        self.assertEqual(public["a"]["api_key"], MASK)
        with patch.dict(os.environ, SECRET_FOR_TEST="real-test-value"):
            self.assertEqual(resolve_browser_endpoints(public, saved)["a"]["api_key"], "real-test-value")
            public["a"]["base_url"] = "https://evil.example.test/v1"
            with self.assertRaises(ValueError):
                resolve_browser_endpoints(public, saved)
            with self.assertRaises(ValueError):
                resolve_browser_endpoints({"x": {"base_url": "https://evil.test", "api_key": "${SECRET_FOR_TEST}"}}, saved)

    def test_csv_neutralizes_formulas_and_redacts_credentials(self):
        register_secrets("test-private-token")
        r = RequestResult("=HYPERLINK(\"https://evil.test\")", "m", "openai", 0, error="\t=1+1 test-private-token")
        row = next(csv.DictReader(io.StringIO(to_csv([r]))))
        self.assertTrue(row["target"].startswith("'="))
        self.assertTrue(row["error"].startswith("'"))
        self.assertNotIn("test-private-token", row["error"])

    def test_proxy_credentials_do_not_enter_history(self):
        from apitest.history import make_ping_record
        rec = make_ping_record("relay", "https://api.example.test/v1/models", [], proxy="http://user:proxy-password@127.0.0.1:8080?token=private")
        serialized = json.dumps(rec)
        self.assertEqual(rec["proxy"], "http://127.0.0.1:8080")
        self.assertNotIn("proxy-password", serialized)
        self.assertNotIn("token=private", serialized)

    def test_output_caps_cannot_be_overridden(self):
        t = target(extra_body={"max_tokens": 999999})
        self.assertEqual(build_body(t, "p", None, 128, None, True)["max_tokens"], 128)
        with self.assertRaises(ValueError):
            build_body(target(extra_body={"n": 10}), "p", None, 128, None, True)
        with self.assertRaises(ValueError):
            target(base_url="http://remote.test")

    def test_efforts_expand_as_distinct_targets(self):
        ts = endpoint_targets("a", {"base_url": "https://example.test", "models": ["m"], "efforts": ["default", "low", "high"]})
        self.assertEqual([t.label for t in ts], ["a@default/m", "a@low/m", "a@high/m"])
        self.assertEqual(build_body(ts[1], "p", None, 1024, None, True)["reasoning_effort"], "low")

    def test_hourly_runs_require_sampling_and_request_cap(self):
        with self.assertRaises(ValueError):
            validate_options(RunOptions("p", duration=3600))
        with self.assertRaises(ValueError):
            validate_options(RunOptions("p", duration=3600, sample_interval=300))
        validate_options(RunOptions("p", duration=3600, sample_interval=300, max_requests=48))

    def test_budget_rejects_missing_prices_and_mixed_currency(self):
        with self.assertRaises(ValueError):
            RunControl([target()], RunOptions("p", budget=1))
        with self.assertRaises(ValueError):
            RunControl([target(), target("b", currency="CNY")], RunOptions("p"))

    def test_visible_tps_excludes_reasoning_and_finish_event_delay(self):
        r = RequestResult("a", "m", "openai", 0, ok=True, ttft=.1, ttft_text=1, total=99, output_tokens=102, reasoning_tokens=100, text_chars=10, text_chunk_times=[1, 2])
        self.assertEqual(r.tps, 2)
        self.assertEqual(r.max_gap, 1)
        self.assertTrue(r.to_dict()["tps_eligible"])
        r.visible_tokens_estimated = True
        self.assertEqual(r.tps, 0)
        self.assertFalse(r.to_dict()["tps_eligible"])

    def test_hourly_buckets_beijing_and_small_sample_caution(self):
        rows = hourly_rows([{"target": "a", "model": "m", "started_at": 0.1, "ok": True, "ttft_text": 1, "tps": 9, "tps_eligible": True}, {"target": "a", "model": "m", "started_at": 0.2, "ok": False}])
        self.assertEqual(rows[0]["hour"], "1970-01-01 08:00")
        self.assertEqual(rows[0]["success_rate"], .5)
        self.assertIsNone(rows[0]["ttft_text_p95"])
        self.assertTrue(rows[0]["small_sample"])

    def test_old_unpriced_history_never_looks_free(self):
        rows = hourly_rows([{"target": "a", "model": "m", "started_at": 1, "ok": True, "cost": None, "cost_uncertain": True, "budget_charge": 0}])
        self.assertIsNone(rows[0]["accounted_cost"])

    def test_all_effort_buttons_expand_as_distinct_targets(self):
        levels = ["default", "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"]
        ts = endpoint_targets("relay", {"base_url": "https://example.test", "models": ["m"], "efforts": levels})
        self.assertEqual(len(ts), 9)
        self.assertEqual(len({t.label for t in ts}), 9)


class AsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_remote_authentication_and_same_origin_controls(self):
        remote = LocalBoundary(web.app.app)
        remote.enable_remote("remote-test-password-123456")
        transport = httpx.ASGITransport(app=remote, client=("192.0.2.55", 12345))
        async with httpx.AsyncClient(transport=transport, base_url="http://192.0.2.10:8787") as c:
            response = await c.get("/")
            self.assertEqual(response.status_code, 401)
            self.assertIn("Basic", response.headers["www-authenticate"])
            self.assertEqual((await c.get("/", auth=("apitest", "wrong"))).status_code, 401)
            self.assertEqual((await c.get("/", headers={"Authorization": "Basic invalid-base64"})).status_code, 401)
            c.auth = ("apitest", "remote-test-password-123456")
            page = await c.get("/")
            self.assertEqual(page.status_code, 200)
            token = re.search(r'name="apitest-token" content="([^"]+)"', page.text)[1]
            self.assertEqual((await c.get("/api/config")).status_code, 200)
            self.assertEqual((await c.post("/api/config", json={}, headers={"X-APITest-Token": token, "Origin": "https://evil.test"})).status_code, 403)
            self.assertEqual((await c.post("/api/config", json={})).status_code, 403)
            # A UI cookie is never a substitute for remote access authentication.
            c.auth = None
            self.assertEqual((await c.get("/api/config")).status_code, 401)

    async def test_local_app_still_rejects_remote_clients(self):
        local = LocalBoundary(web.app.app)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=local, client=("192.0.2.55", 12345)), base_url="http://127.0.0.1") as c:
            self.assertEqual((await c.get("/")).status_code, 403)

    async def test_remote_auth_refuses_weak_password(self):
        with self.assertRaises(ValueError):
            LocalBoundary(web.app.app).enable_remote("short")

    async def test_shared_budget_with_concurrent_requests(self):
        opts = RunOptions("p", budget=.002, max_tokens=100, concurrency=8, rounds=10, order="parallel")
        ts = [target("a", price_in=0, price_out=10), target("b", price_in=0, price_out=10)]
        guard = RunControl(ts, opts)
        tickets = await asyncio.gather(*(guard.reserve(ts[i % 2]) for i in range(20)))
        self.assertEqual(sum(x is not None for x in tickets), 2)
        for ticket in tickets:
            if ticket:
                await guard.settle(ticket, RequestResult("a", "m", "openai", 0, ok=False))
        self.assertAlmostEqual(guard.spent, .002)
        self.assertEqual(guard.unknown, 2)

    async def test_retries_warmups_and_failures_all_count(self):
        calls = []
        def handler(req):
            calls.append(req)
            return httpx.Response(500, text="failed test-credential-123")
        opts = RunOptions("p", rounds=10, warmup=1, retries=2, max_requests=3, budget=1, max_tokens=100)
        seen = []
        with patch("apitest.runner.make_client", lambda _: httpx.AsyncClient(transport=httpx.MockTransport(handler))):
            await run_all([target(price_in=1, price_out=1)], opts, seen.append)
        self.assertEqual(len(calls), 3)
        self.assertEqual(opts.control.attempts, 3)
        self.assertEqual(len(seen), 3)
        self.assertEqual(sum(r.warmup for r in seen), 1)
        self.assertTrue(all("test-credential-123" not in r.error for r in seen))
        self.assertEqual(opts.control.unknown, 3)

    async def test_hourly_quota_recovers_in_next_runtime_hour(self):
        opts = RunOptions("p", hourly_budget=.001, max_tokens=100)
        t = target(price_in=0, price_out=10)
        g = RunControl([t], opts)
        ticket = await g.reserve(t)
        self.assertIsNotNone(ticket)
        await g.settle(ticket, RequestResult("a", "m", "openai", 0))
        self.assertIsNone(await g.reserve(t))
        g.started -= 3601
        self.assertIsNotNone(await g.reserve(t))

    async def test_sampling_stops_at_deadline_without_catchup(self):
        calls = []
        def handler(req):
            calls.append(req)
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=TextStream())
        opts = RunOptions("p", duration=.15, sample_interval=.06, max_requests=10)
        with patch("apitest.runner.make_client", lambda _: httpx.AsyncClient(transport=httpx.MockTransport(handler))):
            rs = await run_all([target()], opts)
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(r.ok and r.reasoning_tokens == 10 for r in rs))
        self.assertTrue(all(not r.to_dict()["tps_eligible"] for r in rs))
        self.assertTrue(all(r.to_dict()["tps_issue"] == "short_window" for r in rs))

    async def test_redirect_does_not_forward_keys(self):
        calls = []
        def handler(req):
            calls.append(req)
            return httpx.Response(307, headers={"location": "https://evil.test"})
        opts = RunOptions("p", rounds=1)
        # Retain make_client settings while replacing only the transport.
        original = httpx.AsyncClient
        def factory(*args, **kwargs):
            return original(*args, transport=httpx.MockTransport(handler), **kwargs)
        with patch("apitest.runner.httpx.AsyncClient", factory):
            rs = await run_all([target(provider="anthropic")], opts)
        self.assertEqual(len(calls), 1)
        self.assertEqual(rs[0].status_code, 307)
        self.assertFalse(rs[0].ok)

    async def test_budget_stops_before_first_unaffordable_request(self):
        calls = []
        def handler(req):
            calls.append(req)
            return httpx.Response(500, text="provider error")
        opts = RunOptions("p", budget=.002, max_tokens=100, rounds=20)
        with patch("apitest.runner.make_client", lambda _: httpx.AsyncClient(transport=httpx.MockTransport(handler))):
            await run_all([target(price_in=0, price_out=10)], opts)
        self.assertEqual(len(calls), 2)
        self.assertEqual(opts.control.stop_reason, "budget")
        self.assertAlmostEqual(opts.control.spent, .002)

    async def test_unpriced_run_is_unknown_not_free(self):
        def handler(req):
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=TextStream())
        opts = RunOptions("p", rounds=1)
        with patch("apitest.runner.make_client", lambda _: httpx.AsyncClient(transport=httpx.MockTransport(handler))):
            rs = await run_all([target()], opts)
        self.assertIsNone(rs[0].cost)
        self.assertTrue(rs[0].cost_uncertain)
        self.assertEqual(opts.control.unknown, 1)
        self.assertIsNone(opts.control.snapshot()["accounted_cost"])
        self.assertEqual(opts.control.snapshot()["cost_status"], "unpriced")
        self.assertEqual(opts.control.snapshot()["unknown_reasons"], {"price_missing": 1})
        self.assertEqual(rs[0].cost_unknown_reason, "price_missing")

    async def test_priced_free_model_reports_real_zero(self):
        opts = RunOptions("p", rounds=1)
        t = target(price_in=0, price_out=0)
        guard = RunControl([t], opts)
        ticket = await guard.reserve(t)
        await guard.settle(ticket, RequestResult(t.name, t.model, t.provider, 0, ok=True, cost=0))
        snap = guard.snapshot()
        self.assertEqual(snap["accounted_cost"], 0)
        self.assertTrue(snap["pricing_complete"])
        self.assertEqual(snap["uncertain_attempts"], 0)
        self.assertEqual(snap["mode"], "rounds")
        self.assertEqual(snap["rounds_per_target"], 1)

    async def test_responses_stream_parser(self):
        data = "\n\n".join("data: " + json.dumps(x) for x in [
            {"type": "response.output_text.delta", "delta": "answer"},
            {"type": "response.completed", "response": {"usage": {"input_tokens": 4, "output_tokens": 12, "output_tokens_details": {"reasoning_tokens": 9}}}},
        ]) + "\n\n"
        response = httpx.Response(200, content=data)
        events = [e async for e in parse_stream("responses", response)]
        self.assertEqual(events[0].text, "answer")
        self.assertEqual(events[1].usage["reasoning"], 9)
        self.assertEqual(build_body(target(provider="responses"), "p", None, 512, None, True)["store"], False)

    async def test_responses_nonstream_request_and_usage(self):
        seen = []
        def handler(req):
            seen.append(req)
            return httpx.Response(200, json={"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "hello"}]}], "usage": {"input_tokens": 2, "output_tokens": 6, "output_tokens_details": {"reasoning_tokens": 4}}})
        opts = RunOptions("p", rounds=1, stream=False, budget=.01, max_tokens=32)
        with patch("apitest.runner.make_client", lambda _: httpx.AsyncClient(transport=httpx.MockTransport(handler))):
            rs = await run_all([target(provider="responses", price_in=1, price_out=1)], opts)
        self.assertEqual(str(seen[0].url), "https://example.test/v1/responses")
        self.assertEqual(json.loads(seen[0].content)["max_output_tokens"], 32)
        self.assertEqual(rs[0].text, "hello")
        self.assertEqual(rs[0].reasoning_tokens, 4)
        self.assertEqual(rs[0].tps, 0)  # Nonstreaming cannot measure decode speed.
        self.assertAlmostEqual(opts.control.spent, .000008)

    async def test_web_boundary_and_masked_config(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "cfg.yaml"
            private_write(cfg, "endpoints:\n  relay:\n    provider: openai\n    base_url: https://api.example.test/v1\n    api_key: test-private-api-key\n    models: [m]\n")
            with patch.object(web, "CONFIG_PATH", cfg):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web.app), base_url="http://127.0.0.1") as c:
                    self.assertEqual((await c.get("/api/config")).status_code, 403)
                    page = await c.get("/")
                    token = re.search(r'name="apitest-token" content="([^"]+)"', page.text)[1]
                    headers = {"X-APITest-Token": token}
                    config = await c.get("/api/config")
                    self.assertNotIn("test-private-api-key", config.text)
                    self.assertEqual(config.json()["endpoints"]["relay"]["api_key"], MASK)
                    self.assertNotIn("test-private-api-key", (await c.get("/api/config/raw")).text)
                    self.assertEqual((await c.post("/api/config", content="{}", headers={**headers, "Content-Type": "text/plain"})).status_code, 403)
                    self.assertEqual((await c.post("/api/config", json={}, headers={**headers, "Origin": "https://evil.test"})).status_code, 403)
                    self.assertEqual((await c.get("/api/config", headers={"Host": "evil.test"})).status_code, 403)
                    self.assertEqual((await c.post("/api/config", json={}, headers={"X-APITest-Token": "wrong"})).status_code, 403)
                    bad = {"endpoints": {"x": {"provider": '<img src=x onerror=alert(1)>', "base_url": "https://api.example.test"}}}
                    self.assertEqual((await c.post("/api/config", json=bad, headers=headers)).status_code, 400)
                    self.assertEqual((await c.post("/api/config", json={"endpoints": config.json()["endpoints"]}, headers=headers)).status_code, 200)
                    self.assertIn("test-private-api-key", cfg.read_text())
                    self.assertNotIn("cdnjs", page.text)
                    self.assertIn("script-src 'self'", page.headers["content-security-policy"])


if __name__ == "__main__":
    unittest.main()
