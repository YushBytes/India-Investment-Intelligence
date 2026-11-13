"""Rate limiting and retries, tested without waiting for a single second.

Every delay in this file is asserted on the number that would have been slept,
not on elapsed time. The clock and the sleep are injected for exactly this
reason: the offline suite stays fast and deterministic while the real
arithmetic is still under test.
"""

import unittest

import _india  # noqa: F401
from indiacore import rate_limit, retry


class Clock(object):
    def __init__(self, now=0.0):
        self.now = now

    def __call__(self):
        return self.now

    def tick(self, seconds):
        self.now += seconds


class Sleeper(object):
    """Records what it was asked to sleep, and advances the clock instead."""

    def __init__(self, clock=None):
        self.calls = []
        self.clock = clock

    def __call__(self, seconds):
        self.calls.append(seconds)
        if self.clock is not None:
            self.clock.tick(seconds)

    @property
    def total(self):
        return sum(self.calls)


class Spacing(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.sleep = Sleeper(self.clock)
        self.limiter = rate_limit.RateLimiter(
            "yahoo", min_interval=1.0, burst=2, clock=self.clock,
            sleeper=self.sleep)

    def test_the_burst_goes_through_without_waiting(self):
        self.assertEqual(self.limiter.acquire(), 0.0)
        self.assertEqual(self.limiter.acquire(), 0.0)
        self.assertEqual(self.sleep.calls, [])

    def test_the_next_request_waits(self):
        self.limiter.acquire()
        self.limiter.acquire()
        waited = self.limiter.acquire()
        self.assertGreater(waited, 0.0)
        self.assertEqual(len(self.sleep.calls), 1)

    def test_waiting_in_real_time_is_never_required_to_test_it(self):
        for _ in range(10):
            self.limiter.acquire()
        self.assertGreater(self.sleep.total, 0.0)
        self.assertEqual(self.limiter.waits, len(self.sleep.calls))

    def test_time_passing_refills_the_allowance(self):
        self.limiter.acquire()
        self.limiter.acquire()
        self.clock.tick(10)
        self.assertEqual(self.limiter.acquire(), 0.0)

    def test_a_zero_interval_never_waits(self):
        open_limiter = rate_limit.RateLimiter("fixture", min_interval=0.0,
                                              clock=self.clock,
                                              sleeper=self.sleep)
        for _ in range(50):
            self.assertEqual(open_limiter.acquire(), 0.0)
        self.assertEqual(self.sleep.calls, [])

    def test_a_caller_can_refuse_to_wait(self):
        self.limiter.acquire()
        self.limiter.acquire()
        self.assertRaises(rate_limit.RateLimited, self.limiter.acquire,
                          False)

    def test_a_burst_below_one_is_refused(self):
        self.assertRaises(ValueError, rate_limit.RateLimiter, "x", 1.0, 0)


class BeingToldOff(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.sleep = Sleeper(self.clock)
        self.limiter = rate_limit.RateLimiter(
            "yahoo", min_interval=1.0, clock=self.clock, sleeper=self.sleep)

    def test_a_cooldown_refuses_rather_than_queueing(self):
        self.limiter.note_rate_limited(30)
        self.assertRaises(rate_limit.RateLimited, self.limiter.acquire)
        self.assertEqual(self.sleep.calls, [],
                         "a rate limited provider must not park a thread")

    def test_retry_after_is_obeyed_to_the_second(self):
        self.assertEqual(self.limiter.note_rate_limited(42), 42.0)
        self.assertEqual(self.limiter.retry_after(), 42.0)

    def test_a_provider_that_sends_nothing_gets_a_long_default(self):
        held = self.limiter.note_rate_limited(None)
        self.assertEqual(held, 60.0)

    def test_an_unreadable_retry_after_falls_back_to_the_default(self):
        self.assertEqual(self.limiter.note_rate_limited("soon"), 60.0)

    def test_the_cooldown_ends_on_its_own(self):
        self.limiter.note_rate_limited(10)
        self.assertTrue(self.limiter.cooling_down)
        self.clock.tick(11)
        self.assertFalse(self.limiter.cooling_down)
        self.assertEqual(self.limiter.acquire(), 0.0)

    def test_the_reason_is_recorded(self):
        self.limiter.note_rate_limited(5, reason="yahoo answered 429")
        self.assertIn("429", self.limiter.stats()["cooldown_reason"])

    def test_refusals_are_counted(self):
        self.limiter.note_rate_limited(5)
        for _ in range(3):
            try:
                self.limiter.acquire()
            except rate_limit.RateLimited:
                pass
        self.assertEqual(self.limiter.stats()["refusals"], 3)


class WhatIsWorthRetrying(unittest.TestCase):
    def test_a_rate_limit_is_retryable(self):
        self.assertEqual(retry.classify_status(429), retry.RATE_LIMITED)

    def test_server_errors_and_timeouts_are_retryable(self):
        for status in (500, 502, 503, 504, 408):
            self.assertEqual(retry.classify_status(status), retry.RETRYABLE,
                             status)

    def test_a_missing_symbol_is_the_answer_and_not_a_fault(self):
        self.assertEqual(retry.classify_status(404), retry.NOT_FOUND)
        self.assertEqual(retry.classify_status(410), retry.NOT_FOUND)

    def test_a_refused_request_is_fatal(self):
        for status in (400, 401, 403, 422):
            self.assertEqual(retry.classify_status(status), retry.FATAL,
                             status)

    def test_no_status_at_all_is_treated_as_retryable(self):
        self.assertEqual(retry.classify_status(None), retry.RETRYABLE)

    def test_success_is_not_a_retry_class(self):
        self.assertEqual(retry.classify_status(200), "")


class Attempts(unittest.TestCase):
    def setUp(self):
        self.sleep = Sleeper()
        self.policy = retry.RetryPolicy(attempts=3, base_delay=1.0,
                                        jitter=0.0, sleeper=self.sleep)

    def boom(self, kind, retry_after=None):
        error = RuntimeError("provider said no")
        error.retry_class = kind
        error.retry_after = retry_after
        raise error

    def test_a_retryable_failure_is_tried_the_full_number_of_times(self):
        calls = []

        def call():
            calls.append(1)
            self.boom(retry.RETRYABLE)

        self.assertRaises(retry.RetryExhausted, self.policy.run, call)
        self.assertEqual(len(calls), 3)

    def test_an_invalid_symbol_is_never_retried(self):
        calls = []

        def call():
            calls.append(1)
            self.boom(retry.NOT_FOUND)

        self.assertRaises(RuntimeError, self.policy.run, call)
        self.assertEqual(len(calls), 1, "a 404 must not be retried")
        self.assertEqual(self.sleep.calls, [])

    def test_a_fatal_failure_is_never_retried(self):
        calls = []

        def call():
            calls.append(1)
            self.boom(retry.FATAL)

        self.assertRaises(RuntimeError, self.policy.run, call)
        self.assertEqual(len(calls), 1)

    def test_the_backoff_doubles(self):
        def call():
            self.boom(retry.RETRYABLE)

        try:
            self.policy.run(call)
        except retry.RetryExhausted:
            pass
        self.assertEqual(self.sleep.calls, [1.0, 2.0])

    def test_the_backoff_is_capped(self):
        policy = retry.RetryPolicy(attempts=8, base_delay=1.0, max_delay=4.0,
                                   jitter=0.0, sleeper=self.sleep)

        def call():
            self.boom(retry.RETRYABLE)

        try:
            policy.run(call)
        except retry.RetryExhausted:
            pass
        self.assertEqual(max(self.sleep.calls), 4.0)

    def test_retry_after_overrides_the_backoff(self):
        def call():
            self.boom(retry.RATE_LIMITED, retry_after=7)

        try:
            self.policy.run(call)
        except retry.RetryExhausted:
            pass
        self.assertEqual(self.sleep.calls, [7.0, 7.0])

    def test_a_rate_limit_tells_the_limiter_before_sleeping(self):
        told = []

        def call():
            self.boom(retry.RATE_LIMITED, retry_after=3)

        try:
            self.policy.run(call, on_rate_limited=told.append)
        except retry.RetryExhausted:
            pass
        self.assertEqual(told, [3, 3, 3])

    def test_a_success_after_a_failure_is_returned(self):
        state = {"n": 0}

        def call():
            state["n"] += 1
            if state["n"] < 3:
                self.boom(retry.RETRYABLE)
            return "the data"

        self.assertEqual(self.policy.run(call), "the data")

    def test_the_history_is_kept_on_the_exhaustion(self):
        def call():
            self.boom(retry.RETRYABLE)

        try:
            self.policy.run(call, describe="yahoo chart for SBIN.NS")
        except retry.RetryExhausted as exc:
            self.assertEqual(len(exc.attempts), 3)
            self.assertIn("SBIN.NS", str(exc))
        else:
            self.fail("expected RetryExhausted")

    def test_jitter_stays_inside_the_band(self):
        policy = retry.RetryPolicy(attempts=2, base_delay=10.0, jitter=0.25,
                                   sleeper=self.sleep)
        for _ in range(20):
            delay = policy.delay_for(1)
            self.assertGreaterEqual(delay, 7.5)
            self.assertLessEqual(delay, 12.5)

    def test_zero_attempts_is_refused(self):
        self.assertRaises(ValueError, retry.RetryPolicy, 0)


class ErrorsDoNotLeakSecrets(unittest.TestCase):
    def test_a_message_mentioning_an_authorization_header_is_withheld(self):
        error = RuntimeError("failed with Authorization: Bearer abc123")
        text = retry._safe(error)
        self.assertNotIn("abc123", text)
        self.assertIn("withheld", text)

    def test_a_message_mentioning_a_token_is_withheld(self):
        self.assertNotIn("sekret",
                         retry._safe(RuntimeError("url?token=sekret")))

    def test_an_ordinary_message_survives(self):
        self.assertIn("timed out", retry._safe(RuntimeError("timed out")))


if __name__ == "__main__":
    unittest.main()
