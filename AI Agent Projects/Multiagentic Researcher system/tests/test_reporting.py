import unittest

import httpx

from multi_agent_assistant.reporting import (
    aggregate_usage,
    extract_usage,
    record_external,
    should_retry,
    timed_node,
)


class ExtractUsageTests(unittest.TestCase):
    def test_reads_token_counts(self):
        usage = extract_usage(
            {"usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}}
        )
        self.assertEqual(
            usage,
            {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
        )

    def test_infers_total_when_missing(self):
        usage = extract_usage(
            {"usage": {"prompt_tokens": 7, "completion_tokens": 5}}
        )
        self.assertEqual(usage["total_tokens"], 12)

    def test_tolerates_absent_usage(self):
        self.assertEqual(
            extract_usage({}),
            {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        )
        self.assertEqual(
            extract_usage(None),
            {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        )

    def test_ignores_non_numeric_values(self):
        usage = extract_usage({"usage": {"prompt_tokens": "x", "completion_tokens": None}})
        self.assertEqual(usage["prompt_tokens"], 0)
        self.assertEqual(usage["completion_tokens"], 0)


class AggregateUsageTests(unittest.TestCase):
    def test_sums_across_calls(self):
        total = aggregate_usage(
            [
                {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
                {"prompt_tokens": 20, "completion_tokens": 7, "total_tokens": 27},
            ]
        )
        self.assertEqual(
            total,
            {"prompt_tokens": 30, "completion_tokens": 10, "total_tokens": 40},
        )

    def test_handles_empty_and_missing_keys(self):
        self.assertEqual(
            aggregate_usage([]),
            {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        )
        total = aggregate_usage([{"total_tokens": 5}])
        self.assertEqual(total["total_tokens"], 5)
        self.assertEqual(total["prompt_tokens"], 0)


class ShouldRetryTests(unittest.TestCase):
    def _status_error(self, code):
        request = httpx.Request("POST", "https://openrouter.ai/x")
        response = httpx.Response(code, request=request)
        return httpx.HTTPStatusError("boom", request=request, response=response)

    def test_retries_timeout_and_transport(self):
        self.assertTrue(should_retry(httpx.ConnectTimeout("t")))
        self.assertTrue(should_retry(httpx.ConnectError("c")))

    def test_retries_5xx_only(self):
        self.assertTrue(should_retry(self._status_error(500)))
        self.assertTrue(should_retry(self._status_error(503)))

    def test_does_not_retry_4xx(self):
        self.assertFalse(should_retry(self._status_error(429)))
        self.assertFalse(should_retry(self._status_error(400)))

    def test_does_not_retry_generic_error(self):
        self.assertFalse(should_retry(ValueError("nope")))


class RecordExternalTests(unittest.TestCase):
    def test_appends_record(self):
        metrics = {}
        record_external(
            metrics,
            provider="Tavily",
            operation="search",
            status="success",
            duration_ms=12.345,
            detail="3 result(s)",
        )
        self.assertEqual(len(metrics["external_api"]), 1)
        record = metrics["external_api"][0]
        self.assertEqual(record["provider"], "Tavily")
        self.assertEqual(record["operation"], "search")
        self.assertEqual(record["status"], "success")
        self.assertEqual(record["duration_ms"], 12.3)  # rounded to 1 decimal
        self.assertEqual(record["detail"], "3 result(s)")

    def test_appends_multiple(self):
        metrics = {}
        record_external(metrics, provider="A", operation="x", status="ok", duration_ms=1)
        record_external(metrics, provider="B", operation="y", status="error", duration_ms=2)
        self.assertEqual([r["provider"] for r in metrics["external_api"]], ["A", "B"])


class TimedNodeTests(unittest.TestCase):
    def test_appends_performance_record_on_success(self):
        def node(state, metrics):
            return {"answer": "ok"}

        wrapped = timed_node("formatter", node)
        result = wrapped({"question": "q"})

        self.assertEqual(result["answer"], "ok")
        self.assertEqual(len(result["performance"]), 1)
        perf = result["performance"][0]
        self.assertEqual(perf["name"], "formatter")
        self.assertEqual(perf["label"], "Result Formatter")
        self.assertEqual(perf["status"], "success")
        self.assertEqual(perf["retries"], 0)
        self.assertGreaterEqual(perf["duration_ms"], 0)

    def test_records_retries_from_metrics(self):
        def node(state, metrics):
            metrics["retries"] = 2
            return {}

        result = timed_node("supervisor", node)({"question": "q"})
        self.assertEqual(result["performance"][0]["retries"], 2)

    def test_preserves_existing_performance_list(self):
        def node(state, metrics):
            return {"performance": [{"name": "prior"}]}

        result = timed_node("weather", node)({"question": "q"})
        names = [p.get("name") for p in result["performance"]]
        self.assertEqual(names, ["prior", "weather"])

    def test_exception_propagates(self):
        def node(state, metrics):
            raise RuntimeError("fail")

        with self.assertRaises(RuntimeError):
            timed_node("supervisor", node)({"question": "q"})


if __name__ == "__main__":
    unittest.main()
