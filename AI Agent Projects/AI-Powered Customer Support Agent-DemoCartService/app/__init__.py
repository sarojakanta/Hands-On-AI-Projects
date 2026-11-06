"""Flask application factory.

Creates and configures the Flask app, loads configuration from the
environment, ensures runtime directories exist, initialises the database, and
registers blueprints. Later phases register additional blueprints (dashboard,
admin) here.
"""

from __future__ import annotations

from flask import Flask

from app.config import config


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)

    # Base configuration from env-driven Config object.
    app.config.update(
        SECRET_KEY=config.SECRET_KEY,
        DEV_MODE=config.DEV_MODE,
        ENV=config.ENV,
        SHADOW_MODE=config.SHADOW_MODE,
        JSON_SORT_KEYS=False,
    )

    # Allow tests to override configuration.
    if test_config:
        app.config.update(test_config)

    # Ensure runtime directories exist before anything touches them.
    config.ensure_dirs()

    # Initialise the database schema (idempotent).
    from app.database.db import init_db

    init_db()

    # Register blueprints.
    from app.routes.chat_routes import chat_bp
    from app.routes.auth_routes import auth_bp
    from app.routes.dashboard_routes import dashboard_bp
    from app.routes.admin_routes import admin_bp
    from app.routes.memory_routes import memory_bp

    app.register_blueprint(chat_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(memory_bp)

    # Clean up the scoped session at the end of each request.
    from app.database.db import SessionLocal

    @app.teardown_appcontext
    def _remove_session(exception=None):  # noqa: ANN001
        SessionLocal.remove()

    return app
