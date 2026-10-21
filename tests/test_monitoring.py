import asyncio
import re
import unittest
from unittest.mock import patch

import httpx

from apitest import web
from apitest.budget import RunControl


class MonitoringAPITests(unittest.IsolatedAsyncioTestCase):
    async def test_monitor_and_temporary_test_can_run_and_stop_independently(self):
        async def fake_run(targets,opts,**kwargs):
            opts.control=RunControl(targets,opts)
            await asyncio.Event().wait()
        with patch.object(web,"RUNS",{}),patch.object(web,"_raw_config",return_value={"endpoints":{}}),patch.object(web,"run_all",fake_run):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web.app),base_url="http://127.0.0.1") as client:
                index=await client.get("/")
                headers={"X-APITest-Token":re.search(r'name="apitest-token" content="([^"]+)"',index.text).group(1)}
                common={"endpoints":{"relay":{"base_url":"https://example.test/v1","models":["model"],"price":{"model":[1,2]}}},"targets":[{"endpoint":"relay","model":"model"}]}
                async def start(options):
                    return await client.post("/api/run",json={**common,"options":options},headers=headers)
                try:
                    monitor=await start({"mode":"monitor","days":1,"sample_minutes":60,"budget":1})
                    self.assertEqual(monitor.status_code,200)
                    temporary=await start({"mode":"test","rounds":1})
                    self.assertEqual(temporary.status_code,200)
                    self.assertEqual(len((await client.get("/api/runs/active")).json()),2)
                    self.assertEqual((await start({"mode":"test","rounds":1})).status_code,409)
                    self.assertEqual((await start({"mode":"monitor","days":1,"sample_minutes":60,"budget":1})).status_code,409)
                    await client.post('/api/run/'+monitor.json()['id']+'/stop',headers=headers,json={})
                    await asyncio.sleep(0)
                    self.assertEqual(web.RUNS[temporary.json()['id']].status,'running')
                    self.assertEqual(web.RUNS[monitor.json()['id']].status,'stopped')
                finally:
                    tasks=[s.task for s in web.RUNS.values() if s.task and not s.task.done()]
                    for task in tasks:task.cancel()
                    await asyncio.gather(*tasks,return_exceptions=True)

    async def request_mode(self, options):
        async def fake_run(targets, opts, **kwargs):
            opts.control = RunControl(targets, opts)
            await asyncio.Event().wait()

        with patch.object(web, "RUNS", {}), patch.object(web, "_raw_config", return_value={"endpoints": {}}), patch.object(web, "run_all", fake_run):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web.app), base_url="http://127.0.0.1") as client:
                self.assertEqual((await client.get("/api/monitors/active")).status_code, 403)
                index = await client.get("/")
                token = re.search(r'name="apitest-token" content="([^"]+)"', index.text).group(1)
                response = await client.post("/api/run", headers={"X-APITest-Token": token}, json={
                    "endpoints": {"relay": {"base_url": "https://example.test/v1", "api_key": "test-private-monitor-key", "models": ["model"], "price": {"model": [1, 2]}}},
                    "targets": [{"endpoint": "relay", "model": "model"}],
                    "options": {"prompt": "private-monitor-prompt", **options}})
                try:
                    await asyncio.sleep(0)
                    active = await client.get("/api/monitors/active")
                    self.assertEqual(active.status_code, 200)
                    self.assertNotIn("private-monitor-prompt", active.text)
                    self.assertNotIn("test-private-monitor-key", active.text)
                    if response.status_code != 200:
                        return response, active.json(), None
                    state = web.RUNS[response.json()["id"]]
                    return response, active.json(), state.opts
                finally:
                    tasks = [state.task for state in web.RUNS.values() if state.task and not state.task.done()]
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)

    async def test_normal_test_does_not_start_a_saved_monitor_schedule(self):
        response, monitors, opts = await self.request_mode({"mode": "test", "days": 2, "hours": 1, "sample_minutes": 5,
            "hourly_budget": 1, "max_requests": 48, "rounds": 3, "monitor_hours": [18]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(monitors, [])
        self.assertIsNone(opts.duration)
        self.assertEqual(opts.sample_interval, 0)
        self.assertIsNone(opts.hourly_budget)
        self.assertIsNone(opts.monitor_hours)
        self.assertEqual(opts.max_requests, 48)

    async def test_active_monitor_has_progress_and_schedule_without_raw_output(self):
        response, monitors, opts = await self.request_mode({"mode": "monitor", "hours": 1, "sample_minutes": 5, "max_requests": 48, "budget": 1, "monitor_hours": [9, 10, 18], "max_tokens": 512})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(monitors), 1)
        monitor = monitors[0]
        self.assertEqual(monitor["id"], response.json()["id"])
        self.assertEqual(monitor["sample_interval"], 300)
        self.assertEqual(monitor["ends_at"] - monitor["started_at"], 3600)
        self.assertIsNone(monitor["budget"]["max_requests"])
        self.assertEqual(monitor["budget"]["budget"], 1)
        self.assertEqual(monitor["completed"], 0)
        self.assertEqual(monitor["monitor_hours"], [9, 10, 18])
        self.assertEqual(monitor["parameters"]["max_tokens"], 512)
        self.assertNotIn("results", monitor)
        self.assertNotIn("meta", monitor)

    async def test_monitor_requires_a_duration_interval_and_budget(self):
        for options in ({"hours": 1, "budget": 1}, {"sample_minutes": 5, "budget": 1}, {"hours": .1, "sample_minutes": 5}, {"days": 7, "sample_minutes": 60, "max_requests": 48}):
            with self.subTest(options=options):
                response, monitors, opts = await self.request_mode({"mode": "monitor", **options})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(monitors, [])
                self.assertIsNone(opts)

    async def test_monitor_days_cover_full_calendar_span(self):
        response, monitors, opts = await self.request_mode({"mode": "monitor", "days": 7,
            "sample_minutes": 60, "hourly_budget": 1, "max_requests": 48, "monitor_hours": [9, 18]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(opts.duration, 7 * 86400)
        self.assertEqual(opts.sample_interval, 3600)
        self.assertEqual(monitors[0]["ends_at"] - monitors[0]["started_at"], 7 * 86400)
        self.assertIsNone(monitors[0]["budget"]["max_requests"])
        self.assertEqual(monitors[0]["budget"]["hourly_budget"], 1)

    async def test_invalid_monitor_days_do_not_create_tasks(self):
        for days in (0, -1, .5, 8, "nan"):
            with self.subTest(days=days):
                response, monitors, opts = await self.request_mode({"mode": "monitor", "days": days,
                    "sample_minutes": 60, "budget": 1})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(monitors, [])
                self.assertIsNone(opts)


if __name__ == "__main__":
    unittest.main()
