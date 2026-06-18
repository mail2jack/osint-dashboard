"""RQ worker entry point.

Usage:
  python3 worker.py [queue1] [queue2]

Starts an RQ worker that processes jobs from the given queues (default: default).
Designed to run as a standalone Docker container alongside the web process.
"""

import os
import sys
import logging

logging.basicConfig(
    level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)

REDIS_URL = os.environ.get("REDIS_URL", "")
if not REDIS_URL:
    logger.error("REDIS_URL not set — RQ worker cannot start")
    sys.exit(1)

try:
    import redis
    from rq import Connection, Worker as RQWorker

    redis_conn = redis.from_url(REDIS_URL)
    queues = sys.argv[1:] if len(sys.argv) > 1 else ["default"]

    logger.info("Starting RQ worker — queues: %s, redis: %s", queues, REDIS_URL)

    with Connection(redis_conn):
        worker = RQWorker(queues)
        worker.work()

except ImportError as e:
    logger.error("Missing dependency: %s — run `pip install rq redis`", e)
    sys.exit(1)
except Exception:
    logger.exception("RQ worker failed to start")
    sys.exit(1)
