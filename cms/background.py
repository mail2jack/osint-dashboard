import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any
from collections.abc import Callable

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="bg")

TaskID = str


def _get_db():
    from .models import db

    return db


def _get_model():
    from .models import BackgroundTask

    return BackgroundTask


def run_in_background(task_id: str, func: Callable, *args, **kwargs) -> None:
    db = _get_db()
    BackgroundTask = _get_model()
    try:
        task = BackgroundTask(
            id=task_id,
            status="pending",
            task_name=func.__name__,
        )
        db.session.add(task)
        db.session.commit()
    except Exception as e:
        logger.warning("Failed to persist background task %s: %s", task_id, e)
        db.session.rollback()
    _executor.submit(_run_task, task_id, func, *args, **kwargs)


def _run_task(task_id: str, func: Callable, *args, **kwargs) -> None:
    db = _get_db()
    try:
        _update_status(task_id, "running")
        result = func(*args, **kwargs)
        _update_status(task_id, "completed", result=result)
    except Exception as e:
        logger.exception("Background task %s failed: %s", task_id, e)
        _update_status(task_id, "failed", error="Internal server error")
    finally:
        db.session.close()


def _update_status(
    task_id: str, status: str, result: Any = None, error: str | None = None
) -> None:
    BackgroundTask = _get_model()
    db = _get_db()
    try:
        task = db.session.get(BackgroundTask, task_id)
        if task:
            task.status = status
            if result is not None:
                task.result = result
            if error is not None:
                task.error = error
            task.updated_at = datetime.now(timezone.utc)
            db.session.commit()
    except Exception as e:
        logger.warning("Failed to update background task %s: %s", task_id, e)
        db.session.rollback()


def get_task_status(task_id: str) -> dict[str, Any] | None:
    BackgroundTask = _get_model()
    db = _get_db()
    try:
        task = db.session.get(BackgroundTask, task_id)
        if task is None:
            return None
        return task.to_dict()
    except Exception as e:
        logger.debug("Failed to get background task %s: %s", task_id, e)
        return None


def cleanup_old_tasks(max_age_hours: int = 24) -> int:
    BackgroundTask = _get_model()
    db = _get_db()
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        hours=max_age_hours
    )
    try:
        deleted = BackgroundTask.query.filter(
            BackgroundTask.created_at < cutoff
        ).delete(synchronize_session="fetch")
        db.session.commit()
        if deleted:
            logger.info("Cleaned up %d old background tasks", deleted)
        return deleted
    except Exception as e:
        logger.debug("Failed to clean up background tasks: %s", e)
        db.session.rollback()
        return 0


def generate_task_id() -> str:
    return uuid.uuid4().hex


def register_background_routes(bp) -> None:
    from flask import jsonify

    @bp.route("/api/background/status/<task_id>")
    def background_task_status(task_id: str):
        status = get_task_status(task_id)
        if status is None:
            return jsonify({"error": "Task not found"}), 404
        return jsonify(status)
