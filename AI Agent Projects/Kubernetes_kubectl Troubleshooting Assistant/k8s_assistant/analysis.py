"""LLM-based analysis of search results into actionable troubleshooting guidance."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from openai import OpenAI

from .search import SearchResponse, SearchResult

_SYSTEM_PROMPT = """\
You are a senior Kubernetes Site Reliability Engineer. You help diagnose
kubectl errors and Kubernetes deployment failures.

You are given a user's problem description and a set of web search results
(Kubernetes docs, Stack Overflow, GitHub issues, cloud provider docs). Use the
search results as evidence. Ground your analysis in them and cite the result
numbers you relied on. If the evidence is thin, say so and reason from your own
Kubernetes expertise, but never invent URLs.

Respond with STRICT JSON only (no markdown, no prose outside the JSON) matching
this schema:

{
  "summary": "one or two sentence plain-language summary of the problem",
  "root_causes": [
    {"cause": "short description", "explanation": "why this happens", "evidence": [1, 3]}
  ],
  "troubleshooting_steps": [
    {"step": "what to do", "detail": "how / what to look for"}
  ],
  "kubectl_commands": [
    {"command": "kubectl ...", "purpose": "what this command reveals or fixes"}
  ],
  "sources": [
    {"ref": 1, "title": "result title", "url": "result url"}
  ]
}

Rules:
- "evidence" and "ref" values are integers that match the [n] result numbers.
- Only include sources you actually referenced.
- Order root_causes from most to least likely.
- Keep commands copy-paste ready; use <placeholders> for user-specific values.
"""


@dataclass
class RootCause:
    cause: str
    explanation: str
    evidence: list[int] = field(default_factory=list)


@dataclass
class TroubleshootingStep:
    step: str
    detail: str = ""


@dataclass
class KubectlCommand:
    command: str
    purpose: str = ""


@dataclass
class SourceRef:
    ref: int
    title: str
    url: str


@dataclass
class TroubleshootingReport:
    summary: str
    root_causes: list[RootCause] = field(default_factory=list)
    troubleshooting_steps: list[TroubleshootingStep] = field(default_factory=list)
    kubectl_commands: list[KubectlCommand] = field(default_factory=list)
    sources: list[SourceRef] = field(default_factory=list)


class AnalysisError(RuntimeError):
    """Raised when the LLM call or response parsing fails."""


class TroubleshootingAnalyzer:
    """Turns raw search results into a structured troubleshooting report."""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        base_url: str | None = None,
    ) -> None:
        if not api_key:
            raise AnalysisError("An OpenAI API key is required.")
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self._model = model

    @staticmethod
    def _build_user_prompt(problem: str, search: SearchResponse) -> str:
        blocks: list[str] = []
        if search.answer:
            blocks.append(f"Tavily summarized answer:\n{search.answer}\n")

        if search.results:
            blocks.append("Search results:")
            blocks.extend(
                r.as_prompt_block(i) for i, r in enumerate(search.results, start=1)
            )
        else:
            blocks.append("No web search results were returned.")

        evidence = "\n".join(blocks)
        return (
            f"User problem:\n{problem.strip()}\n\n"
            f"{evidence}\n\n"
            "Produce the JSON troubleshooting report now."
        )

    # Obvious Kubernetes / kubectl terms. If any appears we skip the LLM
    # classification call and treat the question as in-scope immediately.
    _K8S_KEYWORDS = (
        "kubectl", "kubernetes", "k8s", "pod", "pods", "deployment",
        "namespace", "configmap", "secret", "ingress", "svc", "service",
        "node", "cluster", "helm", "crashloopbackoff", "imagepullbackoff",
        "oomkilled", "kubelet", "statefulset", "daemonset", "replicaset",
        "pvc", "persistentvolume", "container", "etcd", "kube-", "minikube",
        "eks", "aks", "gke", "openshift", "taint", "toleration", "cordon",
    )

    def is_in_scope(self, problem: str) -> bool:
        """Return True if the question is about Kubernetes / kubectl.

        Uses a cheap keyword fast-path first, then a short LLM yes/no
        classification for anything that isn't obviously Kubernetes-related.
        On any classifier error we fail open (treat as in-scope) so a transient
        LLM issue never blocks a legitimate question.
        """
        text = (problem or "").lower()
        if any(kw in text for kw in self._K8S_KEYWORDS):
            return True

        try:
            completion = self._client.chat.completions.create(
                model=self._model,
                temperature=0,
                max_tokens=1,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a classifier. Answer with a single "
                            "character: 'Y' if the user's message is about "
                            "Kubernetes, kubectl, or container orchestration "
                            "troubleshooting; otherwise 'N'. No other output."
                        ),
                    },
                    {"role": "user", "content": problem.strip()},
                ],
            )
        except Exception:  # noqa: BLE001 - fail open on classifier failure
            return True

        answer = (completion.choices[0].message.content or "").strip().upper()
        return not answer.startswith("N")

    def analyze(self, problem: str, search: SearchResponse) -> TroubleshootingReport:
        """Call the LLM and parse its JSON into a TroubleshootingReport."""
        user_prompt = self._build_user_prompt(problem, search)

        try:
            completion = self._client.chat.completions.create(
                model=self._model,
                temperature=0.2,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
            )
        except Exception as exc:  # noqa: BLE001 - surface any OpenAI failure
            raise AnalysisError(f"LLM request failed: {exc}") from exc

        content = completion.choices[0].message.content or ""
        report = self._parse(content)
        return self._backfill_sources(report, search.results)

    @staticmethod
    def _parse(content: str) -> TroubleshootingReport:
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise AnalysisError(
                f"Model did not return valid JSON: {exc}"
            ) from exc

        return TroubleshootingReport(
            summary=data.get("summary", "").strip(),
            root_causes=[
                RootCause(
                    cause=rc.get("cause", "").strip(),
                    explanation=rc.get("explanation", "").strip(),
                    evidence=[int(e) for e in rc.get("evidence", []) if _is_int(e)],
                )
                for rc in data.get("root_causes", [])
            ],
            troubleshooting_steps=[
                TroubleshootingStep(
                    step=ts.get("step", "").strip(),
                    detail=ts.get("detail", "").strip(),
                )
                for ts in data.get("troubleshooting_steps", [])
            ],
            kubectl_commands=[
                KubectlCommand(
                    command=kc.get("command", "").strip(),
                    purpose=kc.get("purpose", "").strip(),
                )
                for kc in data.get("kubectl_commands", [])
            ],
            sources=[
                SourceRef(
                    ref=int(s.get("ref")) if _is_int(s.get("ref")) else 0,
                    title=s.get("title", "").strip(),
                    url=s.get("url", "").strip(),
                )
                for s in data.get("sources", [])
            ],
        )

    @staticmethod
    def _backfill_sources(
        report: TroubleshootingReport, results: list[SearchResult]
    ) -> TroubleshootingReport:
        """Ensure cited sources have accurate titles/URLs from the real results."""
        if report.sources:
            for src in report.sources:
                idx = src.ref - 1
                if 0 <= idx < len(results):
                    src.title = results[idx].title or src.title
                    src.url = results[idx].url or src.url
            return report

        # Model omitted sources; attach all retrieved results as references.
        report.sources = [
            SourceRef(ref=i, title=r.title, url=r.url)
            for i, r in enumerate(results, start=1)
        ]
        return report


def _is_int(value: object) -> bool:
    try:
        int(value)  # type: ignore[arg-type]
        return True
    except (TypeError, ValueError):
        return False
