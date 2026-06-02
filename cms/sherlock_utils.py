import logging
from functools import lru_cache

import requests

from cms.constants import SHERLOCK_DATA_URL

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_sherlock_sites():
    try:
        response = requests.get(SHERLOCK_DATA_URL, timeout=30)
        if response.status_code == 200:
            data = response.json()
            data.pop("$schema", None)
            return data
    except Exception as e:
        logger.error(
            f"Failed to fetch Sherlock sites ({type(e).__name__}): {e}", exc_info=True
        )
    return {}


__all__ = ["get_sherlock_sites"]
