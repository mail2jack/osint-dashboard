"""
Logging configuration — structured JSON logging for production.
Supports both human-readable (dev) and JSON (production) output.
"""

import json
import logging
import os
from logging.handlers import TimedRotatingFileHandler


class JSONFormatter(logging.Formatter):
    """Output log records as JSON lines for log aggregators (ELK, Graylog, etc.)."""

    def format(self, record):
        log_entry = {
            'timestamp': self.formatTime(record),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
            'module': record.module,
            'function': record.funcName,
            'line': record.lineno,
        }
        if hasattr(record, 'request_id'):
            log_entry['request_id'] = record.request_id
        if record.exc_info and record.exc_info[0]:
            log_entry['exception'] = self.formatException(record.exc_info)
        if hasattr(record, 'extra_data'):
            log_entry.update(record.extra_data)
        return json.dumps(log_entry)


class RequestIDFilter(logging.Filter):
    """Add request_id from Flask g to log records."""
    def filter(self, record):
        try:
            from flask import g as flask_g
            record.request_id = getattr(flask_g, 'request_id', '-')
        except Exception:
            record.request_id = '-'
        return True


def setup_logging(app=None):
    """Configure logging. Uses JSON format if LOG_FORMAT=json or in production."""
    log_level = getattr(logging, os.environ.get('LOG_LEVEL', 'INFO').upper(), logging.INFO)
    log_format = os.environ.get('LOG_FORMAT', 'json' if os.environ.get('FLASK_ENV') == 'production' else 'text')

    handlers = [
        TimedRotatingFileHandler('app.log', when='midnight', backupCount=30),
        logging.StreamHandler()
    ]

    if log_format == 'json':
        formatter = JSONFormatter()
    else:
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - [%(request_id)s] %(message)s'
        )

    for handler in handlers:
        handler.setFormatter(formatter)
        handler.addFilter(RequestIDFilter())

    logging.basicConfig(level=log_level, handlers=handlers, force=True)

    # Configure known child loggers — they inherit root handlers but get explicit levels
    for name in ('performance', 'requests'):
        child = logging.getLogger(name)
        child.setLevel(log_level)
        child.propagate = True
