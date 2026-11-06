from langgraph.graph import END, START, StateGraph

from multi_agent_assistant.agents import (
    AssistantState,
    make_formatter,
    make_supervisor,
    make_weather_agent,
    make_web_search_agent,
)
from multi_agent_assistant.config import Settings


def build_graph(settings: Settings):
    graph = StateGraph(AssistantState)
    graph.add_node("supervisor", make_supervisor(settings))
    graph.add_node("web_search", make_web_search_agent(settings))
    graph.add_node("weather", make_weather_agent(settings))
    graph.add_node("formatter", make_formatter(settings))
    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges(
        "supervisor",
        lambda state: state["routes"],
        {
            "direct_answer": "formatter",
            "web_search": "web_search",
            "weather": "weather",
        },
    )
    graph.add_edge("web_search", "formatter")
    graph.add_edge("weather", "formatter")
    graph.add_edge("formatter", END)
    return graph.compile()
