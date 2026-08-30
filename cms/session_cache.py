"""Bounded filesystem cache backend for server-side sessions.

Stock :class:`cachelib.file.FileSystemCache` retries ``PermissionError``
from :func:`open` (and ``os.replace`` / ``os.chmod``) with exponential
backoff for up to 10 seconds before surfacing an ``OSError``.  Under
gunicorn ``sync`` with a single worker, a single request that touches a
session entry the worker cannot open (for example an entry created by a
different UID such as ``root`` during maintenance) blocks the whole app for
~10s; every request that replays the same session id stacks on top, and
``/health`` checks start failing against their timeout.

This subclass keeps the same cache semantics but bounds the retry window to
a few milliseconds, so an unreadable or malformed session entry degrades to
a fast cache miss instead of a stall.  It deliberately only re-bounds the
existing retry loop; keying, serialization, pruning, threshold and file
modes are untouched.
"""

from time import sleep
from typing import Any, Callable

from cachelib.file import FileSystemCache

#: Maximum total wall-clock time a single cache operation (`open`, `os.replace`
#: or `os.chmod`) may spend retrying PermissionError.  Stock cachelib uses
#: 10.0s; any legitimate concurrent-access blip resolves in ~1ms.
SESSION_CACHE_MAX_WAIT = 0.05


class BoundedFileSystemCache(FileSystemCache):
    """``FileSystemCache`` whose access retries are bounded, not 10s."""

    max_wait_time = SESSION_CACHE_MAX_WAIT

    def _run_safely(
        self, fn: Callable[..., Any], *args: Any, **kwargs: Any
    ) -> Any:
        output = None
        wait_step = 0.001
        max_sleep_time = self.max_wait_time
        total_sleep_time = 0.0

        while total_sleep_time < max_sleep_time:
            try:
                output = fn(*args, **kwargs)
            except PermissionError:
                sleep(wait_step)
                total_sleep_time += wait_step
                wait_step *= 2
            else:
                break

        return output