"""Create the database schema and seed sample data.

Usage (from project root, Windows PowerShell):
    python scripts/init_db.py
"""

from __future__ import annotations

import os
import sys

# Ensure the project root is on sys.path when run as a script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database.db import init_db  # noqa: E402
from app.database.seed import seed  # noqa: E402


def main() -> None:
    init_db()
    counts = seed(reset=True)
    print("Database initialised and seeded:")
    for key, value in counts.items():
        print(f"  {key:>10}: {value}")


if __name__ == "__main__":
    main()
