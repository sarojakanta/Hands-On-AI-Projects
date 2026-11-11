"""Tavily Search integration for retrieving Kubernetes troubleshooting resources."""

from __future__ import annotations

from dataclasses import dataclass, field

from tavily import TavilyClient

# Domains that tend to hold authoritative Kubernetes answers. Tavily uses these
# as a soft preference, not a hard filter, so general results still come through.
_PREFERRED_DOMAINS = [
    "kubernetes.io",
    "stackoverflow.com",
    "github.com",
    "redhat.com",
    "aws.amazon.com",
    "cloud.google.com",
    "learn.microsoft.com",
]


@dataclass
class SearchResult:
    """A single web result returned by Tavily."""

    title: str
    url: str
    content: str
    score: float = 0.0

    def as_prompt_block(self, index: int) -> str:
        """Render this result as a numbered block for the LLM prompt."""
        return (
            f"[{index}] {self.title}\n"
            f"URL: {self.url}\n"
            f"Excerpt: {self.content.strip()}\n"
        )


@dataclass
class SearchResponse:
    """Aggregated search output passed downstream to the analysis layer."""

    query: str
    answer: str | None
    results: list[SearchResult] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.results and not self.answer


class SearchError(RuntimeError):
    """Raised when the Tavily request fails."""


class KubernetesSearchClient:
    """Thin wrapper around the Tavily client tuned for Kubernetes queries."""

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise SearchError("A Tavily API key is required.")
        self._client = TavilyClient(api_key=api_key)

    @staticmethod
    def build_query(user_problem: str) -> str:
        """Frame the raw user problem as a focused Kubernetes search query."""
        cleaned = user_problem.strip()
        return f"Kubernetes kubectl troubleshooting: {cleaned}"

    def search(self, user_problem: str, max_results: int = 6) -> SearchResponse:
        """Query Tavily and return normalized results.

        Raises:
            SearchError: if the problem text is empty or the API call fails.
        """
        if not user_problem or not user_problem.strip():
            raise SearchError("Describe the Kubernetes problem before searching.")

        query = self.build_query(user_problem)

        try:
            raw = self._client.search(
                query=query,
                search_depth="advanced",
                include_answer=True,
                max_results=max_results,
                include_domains=_PREFERRED_DOMAINS,
            )
        except Exception as exc:  # noqa: BLE001 - surface any Tavily failure
            raise SearchError(f"Tavily search failed: {exc}") from exc

        results = [
            SearchResult(
                title=item.get("title", "Untitled"),
                url=item.get("url", ""),
                content=item.get("content", ""),
                score=float(item.get("score", 0.0) or 0.0),
            )
            for item in raw.get("results", [])
        ]

        return SearchResponse(
            query=query,
            answer=raw.get("answer"),
            results=results,
        )
