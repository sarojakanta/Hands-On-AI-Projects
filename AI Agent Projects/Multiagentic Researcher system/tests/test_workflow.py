import json
import unittest
from unittest.mock import patch

import httpx

from multi_agent_assistant.config import Settings
from multi_agent_assistant.graph import build_graph


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(
            tavily_api_key="tavily-test",
            openweather_api_key="weather-test",
            openrouter_api_key="openrouter-test",
            openrouter_model="google/gemma-4-26b-a4b-it:free",
        )

    @patch("multi_agent_assistant.agents.httpx.get")
    @patch("multi_agent_assistant.agents.httpx.post")
    def test_stable_historical_question_uses_llm_without_web_or_weather(self, post, get):
        post.side_effect = [
            FakeResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "routes": ["direct_answer"],
                                        "web_query": "",
                                        "weather_location": None,
                                    }
                                )
                            }
                        }
                    ]
                }
            ),
            FakeResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "content": "Neil Armstrong was the first person to walk on the Moon."
                            }
                        }
                    ]
                }
            ),
        ]

        result = build_graph(self.settings).invoke(
            {"question": "Who was the first person to walk on the Moon?"}
        )

        self.assertEqual(
            result["answer"],
            "Neil Armstrong was the first person to walk on the Moon.",
        )
        self.assertEqual(len(post.call_args_list), 2)
        self.assertTrue(
            all(
                call.args[0] == "https://openrouter.ai/api/v1/chat/completions"
                for call in post.call_args_list
            )
        )
        formatter_prompt = post.call_args_list[1].kwargs["json"]["messages"][0][
            "content"
        ]
        self.assertIn("using your own knowledge", formatter_prompt)
        self.assertIn("do not treat it as a lack of information", formatter_prompt)
        get.assert_not_called()

    @patch("multi_agent_assistant.agents.httpx.get")
    @patch("multi_agent_assistant.agents.httpx.post")
    def test_supervisor_runs_both_agents_then_formats_answer(self, post, get):
        post.side_effect = [
            FakeResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "routes": ["web_search", "weather"],
                                        "web_query": "Tokyo weather today",
                                        "weather_location": "Tokyo, JP",
                                    }
                                )
                            }
                        }
                    ]
                }
            ),
            FakeResponse(
                {
                    "results": [
                        {
                            "title": "Tokyo forecast",
                            "url": "https://example.test/tokyo",
                            "content": "Rain expected.",
                        }
                    ]
                }
            ),
            FakeResponse(
                {
                    "choices": [
                        {"message": {"content": "Tokyo is 18 C with light rain."}}
                    ]
                }
            ),
        ]
        get.return_value = FakeResponse(
            {
                "name": "Tokyo",
                "sys": {"country": "JP"},
                "weather": [{"description": "light rain"}],
                "main": {"temp": 18, "feels_like": 17, "humidity": 70},
                "wind": {"speed": 2.5},
            }
        )

        result = build_graph(self.settings).invoke(
            {"question": "What is the weather in Tokyo and today's forecast?"}
        )

        self.assertEqual(result["answer"], "Tokyo is 18 C with light rain.")
        self.assertEqual(len(post.call_args_list), 3)
        self.assertEqual(
            post.call_args_list[1].kwargs["json"]["query"], "Tokyo weather today"
        )
        self.assertEqual(get.call_args.kwargs["params"]["q"], "Tokyo, JP")
        self.assertEqual(get.call_args.kwargs["params"]["units"], "metric")

    @patch("multi_agent_assistant.agents.httpx.post")
    def test_weather_request_without_city_does_not_call_openweather(self, post):
        post.side_effect = [
            FakeResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "routes": ["weather"],
                                        "web_query": "",
                                        "weather_location": None,
                                    }
                                )
                            }
                        }
                    ]
                }
            ),
            FakeResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "content": "Please specify a city for the weather."
                            }
                        }
                    ]
                }
            ),
        ]

        result = build_graph(self.settings).invoke(
            {"question": "What's the weather like?"}
        )

        self.assertIn("specify a city", result["answer"])
        self.assertEqual(len(post.call_args_list), 2)

    @patch("multi_agent_assistant.agents.httpx.post")
    def test_direct_answer_reports_performance_and_token_usage(self, post):
        post.side_effect = [
            FakeResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "routes": ["direct_answer"],
                                        "web_query": "",
                                        "weather_location": None,
                                    }
                                )
                            }
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 20,
                        "total_tokens": 120,
                    },
                }
            ),
            FakeResponse(
                {
                    "choices": [{"message": {"content": "Paris."}}],
                    "usage": {
                        "prompt_tokens": 200,
                        "completion_tokens": 10,
                        "total_tokens": 210,
                    },
                }
            ),
        ]

        result = build_graph(self.settings).invoke(
            {"question": "Capital of France?"}
        )

        perf_names = [p["name"] for p in result["performance"]]
        self.assertEqual(perf_names, ["supervisor", "formatter"])
        self.assertTrue(all(p["status"] == "success" for p in result["performance"]))
        self.assertTrue(all(p["retries"] == 0 for p in result["performance"]))

        usage_agents = {u["agent"]: u for u in result["usage"]}
        self.assertEqual(usage_agents["supervisor"]["total_tokens"], 120)
        self.assertEqual(usage_agents["formatter"]["total_tokens"], 210)
        # No external API calls on the direct-answer path (the reducer channel
        # may exist but stays empty; the web layer omits empty lists).
        self.assertEqual(result.get("external_api", []), [])

    @patch("multi_agent_assistant.agents.httpx.get")
    @patch("multi_agent_assistant.agents.httpx.post")
    def test_weather_path_records_external_api_call(self, post, get):
        post.side_effect = [
            FakeResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "routes": ["weather"],
                                        "web_query": "",
                                        "weather_location": "Tokyo, JP",
                                    }
                                )
                            }
                        }
                    ]
                }
            ),
            FakeResponse(
                {"choices": [{"message": {"content": "Tokyo is 18 C."}}]}
            ),
        ]
        get.return_value = FakeResponse(
            {
                "name": "Tokyo",
                "sys": {"country": "JP"},
                "weather": [{"description": "light rain"}],
                "main": {"temp": 18, "feels_like": 17, "humidity": 70},
                "wind": {"speed": 2.5},
            }
        )

        result = build_graph(self.settings).invoke(
            {"question": "Weather in Tokyo?"}
        )

        self.assertEqual(len(result["external_api"]), 1)
        call = result["external_api"][0]
        self.assertEqual(call["provider"], "OpenWeather")
        self.assertEqual(call["operation"], "current")
        self.assertEqual(call["status"], "success")
        self.assertIn("Tokyo", call["detail"])

    @patch("multi_agent_assistant.agents.retry_backoff", lambda attempt: None)
    @patch("multi_agent_assistant.agents.httpx.post")
    def test_transient_server_error_is_retried_and_counted(self, post):
        request = httpx.Request(
            "POST", "https://openrouter.ai/api/v1/chat/completions"
        )

        class FailingResponse:
            def raise_for_status(self):
                raise httpx.HTTPStatusError(
                    "server error",
                    request=request,
                    response=httpx.Response(503, request=request),
                )

            def json(self):  # pragma: no cover - never reached
                return {}

        supervisor_ok = FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "routes": ["direct_answer"],
                                    "web_query": "",
                                    "weather_location": None,
                                }
                            )
                        }
                    }
                ]
            }
        )
        formatter_ok = FakeResponse(
            {"choices": [{"message": {"content": "Answer."}}]}
        )
        # Supervisor: fail once (503), then succeed. Formatter: succeed.
        post.side_effect = [FailingResponse(), supervisor_ok, formatter_ok]

        result = build_graph(self.settings).invoke({"question": "Hi?"})

        self.assertEqual(result["answer"], "Answer.")
        supervisor_perf = next(
            p for p in result["performance"] if p["name"] == "supervisor"
        )
        self.assertEqual(supervisor_perf["retries"], 1)


if __name__ == "__main__":
    unittest.main()
