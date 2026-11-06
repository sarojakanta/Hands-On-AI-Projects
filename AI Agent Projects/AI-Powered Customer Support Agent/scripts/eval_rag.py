"""Run the RAG evaluation harness and print metrics (R-8).

Usage (from project root):
    python scripts/eval_rag.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.rag.evaluation import evaluate  # noqa: E402


def main() -> None:
    result = evaluate()
    print("RAG evaluation (golden questions)")
    print(f"  recall@4:     {result.recall_at_4:.1%}  ({result.recall_hits}/{result.total})  target >= 90%")
    print(f"  faithfulness: {result.faithfulness:.1%}  ({result.faithful_hits}/{result.total})  target >= 95%")


if __name__ == "__main__":
    main()
