import asyncio
from datetime import datetime
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx

from apitest import runner
from apitest.budget import validate_options
from apitest.providers import Target
from apitest.runner import RunOptions, run_all
from apitest.scheduling import TIMEZONE, is_active, next_active, validate_hours


def stamp(hour, minute=0, second=0, day=20):
    return datetime(2026, 10, day, hour, minute, second, tzinfo=TIMEZONE).timestamp()


class SchedulingTests(unittest.TestCase):
    def test_hour_boundaries_and_midnight(self):
        self.assertTrue(is_active(stamp(18), [18, 19]))
        self.assertTrue(is_active(stamp(19, 59, 59), [18, 19]))
        self.assertFalse(is_active(stamp(20), [18, 19]))
        self.assertEqual(next_active(stamp(20), [18, 19]), stamp(18, day=21))
        self.assertEqual(next_active(stamp(23, 59, 59), [0]), stamp(0, day=21))
        self.assertEqual(next_active(stamp(9, 12), [9]), stamp(9, 12))

    def test_all_day_and_split_windows(self):
        for hour in range(24):
            self.assertTrue(is_active(stamp(hour), list(range(24))))
            self.assertTrue(is_active(stamp(hour), None))
        self.assertEqual(next_active(stamp(12), [9, 10, 11, 14, 15, 20]), stamp(14))

    def test_invalid_or_empty_windows_are_rejected(self):
        for hours in ([], [24], [-1], [True], [1, 1], ["18"], "18"):
            with self.subTest(hours=hours), self.assertRaises(ValueError):
                validate_hours(hours)
        with self.assertRaises(ValueError):
            validate_options(RunOptions("p", monitor_hours=[18]))


class Clock:
    def __init__(self, wall):
        self.wall = wall
        self.elapsed = 0
        self.waits = []

    def advance(self, seconds):
        self.wall += seconds
        self.elapsed += seconds

    async def sleep(self, seconds):
        self.waits.append(seconds)
        self.advance(seconds)


class ScheduleExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def execute(self, wall, hours, duration, *, warmup=0, retries=0, handler_delay=0, status=200, max_requests=10, budget=None):
        clock, calls = Clock(wall), []
        opts = RunOptions("p", duration=duration, sample_interval=60, monitor_hours=hours,
            max_requests=max_requests, budget=budget, stream=False, warmup=warmup, retries=retries)
        target = Target("relay", "openai", "https://example.test/v1", "test-only", "model", price_in=1, price_out=1)
        def handler(request):
            calls.append(clock.wall)
            clock.advance(handler_delay)
            return httpx.Response(status, json={"choices": [{"message": {"content": "hello"}}], "usage": {"prompt_tokens": 10, "completion_tokens": 10}})
        def make_client(_):
            return httpx.AsyncClient(transport=httpx.MockTransport(handler))
        fake_time = SimpleNamespace(time=lambda: clock.wall, monotonic=lambda: clock.elapsed, perf_counter=lambda: clock.elapsed)
        fake_asyncio = SimpleNamespace(sleep=clock.sleep, Semaphore=asyncio.Semaphore, wait_for=asyncio.wait_for,
            TimeoutError=asyncio.TimeoutError, CancelledError=asyncio.CancelledError, gather=asyncio.gather)
        with patch.object(runner, "time", fake_time), patch.object(runner, "asyncio", fake_asyncio), patch.object(runner, "make_client", make_client):
            results = await run_all([target], opts)
        return opts, clock, calls, results

    async def test_excluded_period_makes_no_calls_including_warmups(self):
        opts, clock, calls, results = await self.execute(stamp(5), [18], 120, warmup=2, retries=2)
        self.assertEqual(calls, [])
        self.assertEqual(results, [])
        self.assertEqual(opts.control.attempts, 0)
        self.assertEqual(opts.control.stop_reason, "duration")
        self.assertTrue(all(seconds <= 30 for seconds in clock.waits))

    async def test_wakes_at_selected_hour_without_catchup(self):
        opts, clock, calls, results = await self.execute(stamp(17, 59, 50), [18], 100)
        self.assertEqual(calls, [stamp(18), stamp(18, 0, 50)])
        self.assertEqual(opts.control.attempts, 2)
        self.assertEqual(len(results), 2)
        self.assertTrue(all(is_active(at, [18]) for at in calls))

    async def test_retry_does_not_start_after_window_closes(self):
        opts, clock, calls, results = await self.execute(stamp(17, 59, 59), [17], 120, retries=2, handler_delay=2, status=500)
        self.assertEqual(calls, [stamp(17, 59, 59)])
        self.assertEqual(opts.control.attempts, 1)
        self.assertEqual(len(results), 1)

    async def test_midnight_opens_next_selected_window(self):
        opts, clock, calls, results = await self.execute(stamp(23, 59, 50), [0], 70)
        self.assertEqual(calls[0], stamp(0, day=21))
        self.assertTrue(all(is_active(at, [0]) for at in calls))

    async def test_request_cap_ends_without_waiting_for_another_window(self):
        opts, clock, calls, results = await self.execute(stamp(17, 59, 59), [17], 86400, max_requests=1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(clock.waits, [])
        self.assertEqual(opts.control.stop_reason, "max_requests")

    async def test_budget_monitor_continues_beyond_48_calls_until_duration(self):
        opts, clock, calls, results = await self.execute(stamp(18), [18, 19], 7200, max_requests=None, budget=1)
        self.assertEqual(len(calls), 120)
        self.assertEqual(len(results), 120)
        self.assertEqual(opts.control.attempts, 120)
        self.assertLess(opts.control.spent, 1)
        self.assertEqual(opts.control.stop_reason, "duration")


if __name__ == "__main__":
    unittest.main()
