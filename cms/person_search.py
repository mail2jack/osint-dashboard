import logging

from cms.services.search_service import person_dorks_search

logger = logging.getLogger(__name__)


async def search_person_async(full_name, progress_callback=None):
    """Modern person search using Brave API (replaces broken scrapers).

    Returns the same format as person_dorks_search() so the SSE frontend
    continues to work: {search_links, dorks_results, total_results, ...}.
    """
    return person_dorks_search(full_name)


__all__ = ["search_person_async"]
