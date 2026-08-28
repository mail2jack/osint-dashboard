"""
Alembic env.py — imports models directly, bypassing app.py to avoid
circular initialization (data migrations that query tables before
Alembic creates them).

Database URL is read from DATABASE_URL env var, matching the Flask app.
"""

import logging
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

logger = logging.getLogger("alembic.env")

# Read DATABASE_URL from environment (same source as Flask app)
import os

database_url = os.environ.get("DATABASE_URL")
if database_url:
    # Resolve relative SQLite paths to absolute to match app.py's own config,
    # so Alembic and the Flask engine always point at the same file.
    if database_url.startswith("sqlite:///") and not database_url.startswith(
        "sqlite:////"
    ):
        rel_path = database_url[len("sqlite:///") :]
        abs_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", rel_path)
        )
        config.set_main_option("sqlalchemy.url", f"sqlite:///{abs_path}")
    else:
        config.set_main_option("sqlalchemy.url", database_url)
else:
    # No DATABASE_URL set: match app.py's dev-only SQLite fallback instead of
    # leaving the `driver://user:pass@localhost/dbname` placeholder from
    # alembic.ini (which cannot be loaded as a dialect).
    from pathlib import Path

    cms_db_path = Path(__file__).resolve().parent.parent / "cms.db"
    config.set_main_option("sqlalchemy.url", f"sqlite:///{cms_db_path}")

# Import all models to register them with SQLAlchemy metadata
# This must happen before target_metadata is assigned.
# We import cms.models directly rather than going through app.py,
# which would trigger create_cms_module() → init_default_settings()
# before Alembic has created any tables.
try:
    from cms.models import db
    import cms.models  # noqa: F401 — registers all ORM models

    target_metadata = db.metadata
except Exception:
    logger.error("Failed to import CMS models. Ensure DATABASE_URL is set.")
    raise


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
