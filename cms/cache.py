"""
Cache for OSINT results.

Backed by Redis when REDIS_URL is set, otherwise falls back to filesystem cache.
"""

from .redis_cache import get, set, invalidate, get_status

__all__ = ["get", "set", "invalidate", "get_status"]
