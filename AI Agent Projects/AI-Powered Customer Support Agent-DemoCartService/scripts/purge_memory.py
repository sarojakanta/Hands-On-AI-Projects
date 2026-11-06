"""Purge expired episodic customer memory (12-month retention).

Run on a schedule (e.g. daily cron) in production. In the demo it can be run
manually:
    python scripts/purge_memory.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.customer_memory_service import purge_expired  # noqa: E402


def main() -> None:
    count = purge_expired()
    print(f"Purged {count} expired episodic memory record(s).")


if __name__ == "__main__":
    main()
