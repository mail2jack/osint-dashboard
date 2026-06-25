import os
import sqlite3
import threading
from datetime import datetime

from flask import Blueprint
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, scoped_session, declarative_base

workflow_bp = Blueprint(
    "workflow",
    __name__,
    template_folder="../../templates/cms/workflow",
    url_prefix="/cms/workflow",
)

_db_initialized = threading.Event()
_base = declarative_base()
_engine = None
_session_factory = None

WORKFLOW_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "instance",
    "workflow.db",
)


def get_engine():
    global _engine
    if _engine is None:
        os.makedirs(os.path.dirname(WORKFLOW_DB_PATH), exist_ok=True)
        _engine = create_engine(f"sqlite:///{WORKFLOW_DB_PATH}", echo=False)

        @event.listens_for(_engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return _engine


def get_session():
    global _session_factory
    if _session_factory is None:
        _session_factory = scoped_session(sessionmaker(bind=get_engine()))
    return _session_factory()


def init_db():
    global _base
    _base.metadata.create_all(get_engine())
    _db_initialized.set()


def ensure_db():
    if not _db_initialized.is_set():
        init_db()
    _migrate()


def _migrate():
    """Apply schema migrations for existing databases."""
    import sqlalchemy as sa

    engine = get_engine()
    insp = sa.inspect(engine)
    cols = [c["name"] for c in insp.get_columns("workflow_cases")]
    if "pv_body" not in cols:
        with engine.connect() as conn:
            conn.execute(sa.text("ALTER TABLE workflow_cases ADD COLUMN pv_body TEXT"))
            conn.commit()
    action_cols = [c["name"] for c in insp.get_columns("workflow_research_actions")]
    if "data_value" not in action_cols:
        with engine.connect() as conn:
            conn.execute(
                sa.text(
                    "ALTER TABLE workflow_research_actions ADD COLUMN data_value TEXT"
                )
            )
            conn.commit()
    if "cancel_requested" not in action_cols:
        with engine.connect() as conn:
            conn.execute(
                sa.text(
                    "ALTER TABLE workflow_research_actions ADD COLUMN cancel_requested BOOLEAN DEFAULT 0"
                )
            )
            conn.commit()
    try:
        finding_cols = [c["name"] for c in insp.get_columns("workflow_findings")]
        if "comment" not in finding_cols:
            with engine.connect() as conn:
                conn.execute(
                    sa.text("ALTER TABLE workflow_findings ADD COLUMN comment TEXT")
                )
                conn.commit()
        if "archived_at" not in finding_cols:
            with engine.connect() as conn:
                conn.execute(
                    sa.text(
                        "ALTER TABLE workflow_findings ADD COLUMN archived_at TIMESTAMP"
                    )
                )
                conn.commit()
        if "raw_data" not in finding_cols:
            with engine.connect() as conn:
                conn.execute(
                    sa.text("ALTER TABLE workflow_findings ADD COLUMN raw_data JSON")
                )
                conn.commit()
    except Exception:
        pass


from . import models, routes  # noqa: E402, F811
