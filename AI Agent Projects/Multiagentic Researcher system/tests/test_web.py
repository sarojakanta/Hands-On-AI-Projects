import unittest
from unittest.mock import Mock, patch

import httpx

from multi_agent_assistant.config import Settings
from multi_agent_assistant.web import create_app


class FakeGraph:
    def __init__(self):
        self.questions = []

    def invoke(self, state):
        self.questions.append(state["question"])
        return {"answer": f"Answer to: {state['question']}"}


class WebAppTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(
            tavily_api_key="tavily-test",
            openweather_api_key="weather-test",
            openrouter_api_key="openrouter-test",
            openrouter_model="google/gemma-4-26b-a4b-it:free",
        )
        self.graph = FakeGraph()
        self.build_graph = patch(
            "multi_agent_assistant.web.build_graph", return_value=self.graph
        )
        self.build_graph_mock = self.build_graph.start()
        self.addCleanup(self.build_graph.stop)
        self.client = create_app(self.settings).test_client()

    def test_homepage_renders_chat_interface_and_assets(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"What can I help you with?", response.data)
        self.assertIn(b"Supervisor", response.data)
        self.assertIn(b"Web Search", response.data)
        self.assertIn(b"Weather", response.data)
        self.assertIn(b"Result Formatter", response.data)
        self.assertIn(b"/static/styles.css", response.data)
        script_response = self.client.get("/static/app.js")
        self.assertEqual(script_response.status_code, 200)
        script_response.close()

    def test_chat_invokes_graph_and_reuses_compiled_graph(self):
        first = self.client.post("/api/chat", json={"question": "  Hello  "})
        second = self.client.post("/api/chat", json={"question": "How are you?"})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json, {"answer": "Answer to: Hello"})
        self.assertEqual(second.status_code, 200)
        self.assertEqual(self.graph.questions, ["Hello", "How are you?"])
        self.build_graph_mock.assert_called_once_with(self.settings)

    def test_chat_rejects_empty_or_non_json_questions(self):
        empty = self.client.post("/api/chat", json={"question": "  "})
        non_json = self.client.post("/api/chat", data="not json")

        self.assertEqual(empty.status_code, 400)
        self.assertIn("Enter a question", empty.json["error"])
        self.assertEqual(non_json.status_code, 400)
        self.assertIn("JSON", non_json.json["error"])
        self.build_graph_mock.assert_not_called()

    def test_chat_rejects_question_over_character_limit(self):
        response = self.client.post("/api/chat", json={"question": "x" * 4001})

        self.assertEqual(response.status_code, 400)
        self.assertIn("4000 characters", response.json["error"])
        self.build_graph_mock.assert_not_called()

    @patch("multi_agent_assistant.web.Settings.from_env")
    def test_chat_reports_missing_api_keys_without_running_graph(self, from_env):
        from_env.side_effect = ValueError(
            "Missing required environment variables: OPENROUTER_API_KEY"
        )

        response = create_app().test_client().post(
            "/api/chat", json={"question": "Hello"}
        )

        self.assertEqual(response.status_code, 503)
        self.assertIn("OPENROUTER_API_KEY", response.json["error"])
        self.build_graph_mock.assert_not_called()

    def test_chat_passes_through_report_fields_and_aggregates_usage(self):
        rich_result = {
            "answer": "Paris.",
            "agents": [
                {"name": "supervisor", "label": "Supervisor", "detail": "Routed"},
                {"name": "formatter", "label": "Result Formatter", "detail": "Done"},
            ],
            "usage": [
                {"agent": "supervisor", "prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
                {"agent": "formatter", "prompt_tokens": 200, "completion_tokens": 10, "total_tokens": 210},
            ],
            "performance": [
                {"name": "supervisor", "label": "Supervisor", "duration_ms": 5.0, "status": "success", "retries": 0},
                {"name": "formatter", "label": "Result Formatter", "duration_ms": 8.0, "status": "success", "retries": 1},
            ],
            "external_api": [
                {"provider": "OpenWeather", "operation": "current", "status": "success", "duration_ms": 3.0, "detail": "Tokyo, JP"},
            ],
        }
        self.graph.invoke = Mock(return_value=rich_result)

        response = self.client.post("/api/chat", json={"question": "Capital of France?"})
        body = response.json

        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["answer"], "Paris.")
        self.assertEqual(len(body["agents"]), 2)
        self.assertEqual(len(body["performance"]), 2)
        self.assertEqual(body["external_api"][0]["provider"], "OpenWeather")
        self.assertEqual(
            body["usage_total"],
            {"prompt_tokens": 300, "completion_tokens": 30, "total_tokens": 330},
        )

    def test_chat_omits_report_fields_when_absent(self):
        # The default FakeGraph returns only an answer; no report keys expected.
        response = self.client.post("/api/chat", json={"question": "Hello"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json.keys()), {"answer"})

    def test_chat_explains_openrouter_rate_limit(self):
        request = httpx.Request(
            "POST", "https://openrouter.ai/api/v1/chat/completions"
        )
        response = httpx.Response(429, request=request)
        self.graph.invoke = Mock(
            side_effect=httpx.HTTPStatusError(
                "rate limited", request=request, response=response
            )
        )

        result = self.client.post("/api/chat", json={"question": "Weather in Tokyo?"})

        self.assertEqual(result.status_code, 503)
        self.assertIn("OpenRouter", result.json["error"])
        self.assertIn("rate-limited", result.json["error"])


if __name__ == "__main__":
    unittest.main()
