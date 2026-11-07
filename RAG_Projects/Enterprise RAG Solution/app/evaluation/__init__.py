"""Retrieval and answer evaluation against a golden question set (``python -m app.evaluation``)."""

from app.evaluation.runner import EvalItem, EvalReport, evaluate, load_dataset

__all__ = ["EvalItem", "EvalReport", "evaluate", "load_dataset"]
