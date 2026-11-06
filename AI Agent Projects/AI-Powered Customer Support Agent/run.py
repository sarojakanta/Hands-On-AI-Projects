"""Application entry point.

Usage (Windows PowerShell):
    python run.py
"""

from __future__ import annotations

from app import create_app
from app.config import config

app = create_app()

if __name__ == "__main__":
    app.run(host=config.HOST, port=config.PORT, debug=config.DEV_MODE)
