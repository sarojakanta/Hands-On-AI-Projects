import logging
import os
from typing import Any

import httpx
from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import BadRequest

from multi_agent_assistant.config import Settings
from multi_agent_assistant.graph import build_graph
from multi_agent_assistant.reporting import aggregate_usage

logger = logging.getLogger(__name__)
MAX_QUESTION_LENGTH = 4000


def create_app(settings: Settings | None = None) -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024
    app.extensions["assistant_settings"] = settings
    app.extensions["assistant_graph"] = None

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.post("/api/chat")
    def chat():
        if not request.is_json:
            return jsonify(error="Send a JSON request with a question field."), 400
        try:
            payload: Any = request.get_json()
        except BadRequest:
            return jsonify(error="The request body must contain valid JSON."), 400
        if not isinstance(payload, dict):
            return jsonify(error="The request body must be a JSON object."), 400

        question = payload.get("question")
        if not isinstance(question, str) or not question.strip():
            return jsonify(error="Enter a question before sending."), 400
        question = question.strip()
        if len(question) > MAX_QUESTION_LENGTH:
            return jsonify(
                error=f"Questions must be {MAX_QUESTION_LENGTH} characters or fewer."
            ), 400

        try:
            graph = app.extensions["assistant_graph"]
            if graph is None:
                configured_settings = app.extensions["assistant_settings"]
                if configured_settings is None:
                    try:
                        configured_settings = Settings.from_env()
                    except ValueError as exc:
                        logger.warning("Assistant configuration error: %s", exc)
                        return jsonify(error=str(exc)), 503
                    app.extensions["assistant_settings"] = configured_settings
                graph = build_graph(configured_settings)
                app.extensions["assistant_graph"] = graph
            result = graph.invoke({"question": question})
            answer = result.get("answer")
            if not isinstance(answer, str) or not answer.strip():
                raise RuntimeError("The assistant returned an empty answer.")
            response_body: dict[str, Any] = {"answer": answer}
            agents = result.get("agents")
            if isinstance(agents, list) and agents:
                response_body["agents"] = agents
            usage = result.get("usage")
            if isinstance(usage, list) and usage:
                response_body["usage"] = usage
                response_body["usage_total"] = aggregate_usage(usage)
            performance = result.get("performance")
            if isinstance(performance, list) and performance:
                response_body["performance"] = performance
            external_api = result.get("external_api")
            if isinstance(external_api, list) and external_api:
                response_body["external_api"] = external_api
            return jsonify(**response_body)
        except httpx.HTTPStatusError as exc:
            provider = exc.request.url.host
            status_code = exc.response.status_code
            logger.warning("Upstream request to %s failed with HTTP %s.", provider, status_code)
            if provider == "openrouter.ai" and status_code == 429:
                return jsonify(
                    error=(
                        "OpenRouter has rate-limited the configured model. Wait a "
                        "little and try again, or choose a model with available "
                        "quota in OPENROUTER_MODEL."
                    )
                ), 503
            return jsonify(
                error=(
                    f"The {provider} service returned HTTP {status_code}. Check "
                    "that service's API key and quota, then try again."
                )
            ), 502
        except Exception:
            logger.exception("Assistant request failed.")
            return jsonify(
                error="The assistant could not complete that request. Check the "
                "server logs and API configuration, then try again."
            ), 502

    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # Bind to localhost by default for safe local runs. In a container, set
    # WEB_HOST=0.0.0.0 (the Dockerfile does this) so the port is reachable.
    host = os.getenv("WEB_HOST", "127.0.0.1")
    port = int(os.getenv("WEB_PORT", "5000"))
    create_app().run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
