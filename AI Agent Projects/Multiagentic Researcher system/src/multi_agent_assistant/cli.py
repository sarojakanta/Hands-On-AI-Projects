import argparse

from multi_agent_assistant.config import Settings
from multi_agent_assistant.graph import build_graph


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ask a question using web search and weather agents."
    )
    parser.add_argument("question", nargs="+", help="Question to answer")
    args = parser.parse_args()

    settings = Settings.from_env()
    result = build_graph(settings).invoke({"question": " ".join(args.question)})
    print(result["answer"])
