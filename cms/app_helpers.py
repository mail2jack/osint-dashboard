from dotenv import load_dotenv

load_dotenv()

from cms.constants import (  # noqa: E402, F401
    CACHE_TTL_HOURS,
    FALSE_POSITIVE_PATTERNS,
    CONFIRMATION_PATTERNS,
    PLATFORM_PRIORITY,
    HEADERS,
    WHATSAPP_HEADERS,
    SHERLOCK_DATA_URL,
    WEBCAM_DATA,
)

from cms.logging_utils import (  # noqa: E402, F401
    perf_logger,
    req_logger,
    logger,
    log_performance,
    log_request,
)

from cms.cache_utils import (  # noqa: E402, F401
    result_cache,
    get_cache_key,
)

from cms.validators import (  # noqa: E402, F401
    verify_profile,
    validate_email,
    validate_ip,
    validate_domain,
    interpolate_string,
    normalize_phone_number,
)

from cms.api_keys import (  # noqa: E402, F401
    _get_overheid_key,
    _get_twochat_credentials,
    _get_brave_key,
    _get_hibp_key,
)

from cms.search_tracking import (  # noqa: E402, F401
    SearchJob,
    search_registry,
    active_searches,
    _searches_lock,
    _active_user_searches,
    _active_user_searches_lock,
    MAX_CONCURRENT_SEARCHES_PER_USER,
    search_request_counts,
    acquire_search_slot,
    release_search_slot,
    increment_request_count,
    get_maigret_database,
    get_maigret_sites_dict,
)

from cms.sherlock_utils import get_sherlock_sites  # noqa: E402, F401

from cms.email_search import (  # noqa: E402, F401
    check_site_with_retry,
    check_site_email,
    check_email_site,
    search_email_async,
    lookup_email,
    search_email_holehe,
    search_email_combined,
    calculate_confidence_score,
    cross_validate_results,
)

from cms.username_search import (  # noqa: E402, F401
    check_username_async,
    check_sherlock_site,
    search_username_async,
    search_username,
    check_whatsmyname_site,
    search_username_whatsmyname,
)

from cms.ip_domain_lookup import lookup_ip, lookup_domain  # noqa: E402, F401

from cms.person_search import search_person_async  # noqa: E402, F401

from cms.pdf_report import generate_results_pdf  # noqa: E402, F401

from cms.ollama_utils import check_ollama_available  # noqa: E402, F401


__all__ = [
    "active_searches",
    "_searches_lock",
    "_active_user_searches",
    "_active_user_searches_lock",
    "MAX_CONCURRENT_SEARCHES_PER_USER",
    "acquire_search_slot",
    "release_search_slot",
    "get_sherlock_sites",
    "search_email_async",
    "search_email_holehe",
    "search_email_combined",
    "lookup_email",
    "search_username",
    "search_username_async",
    "lookup_ip",
    "lookup_domain",
    "_get_overheid_key",
    "_get_twochat_credentials",
    "_get_brave_key",
    "_get_hibp_key",
    "check_ollama_available",
    "WEBCAM_DATA",
    "SearchJob",
    "search_registry",
    "normalize_phone_number",
    "generate_results_pdf",
]
