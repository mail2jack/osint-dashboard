"""Integration tests for the rate limiting module."""

from cms.rate_limiting import (
    rate_limit,
    get_api_rate_limit_status,
    is_rate_limited,
    set_rate_limited,
    get_rate_limit_status,
    cleanup_rate_limits,
    DEFAULT_RATE_LIMIT,
    STRICT_RATE_LIMIT,
)


class TestRateLimiter:
    def test_default_limit_constant(self):
        assert DEFAULT_RATE_LIMIT == (100, 60)

    def test_strict_limit_constant(self):
        assert STRICT_RATE_LIMIT == (30, 60)


class TestPlatformRateLimiter:
    def test_not_limited_by_default(self):
        limited, _ = is_rate_limited("test_site")
        assert not limited

    def test_set_rate_limited_marks_as_limited(self):
        set_rate_limited("test_site_2", retry_after=30)
        limited, data = is_rate_limited("test_site_2")
        assert limited
        assert data["count"] == 1

    def test_expired_limit(self):
        set_rate_limited("test_site_3", retry_after=0)
        limited, _ = is_rate_limited("test_site_3")
        assert not limited

    def test_get_rate_limit_status(self):
        set_rate_limited("test_site_4", retry_after=60)
        status = get_rate_limit_status()
        names = [s["site"] for s in status]
        assert "test_site_4" in names

    def test_cleanup_rate_limits(self):
        set_rate_limited("test_site_5", retry_after=0)
        cleanup_rate_limits(max_age_seconds=0)
        status = get_rate_limit_status()
        names = [s["site"] for s in status]
        assert "test_site_5" not in names


class TestAPIRateLimiter:
    def test_get_api_rate_limit_status_returns_list(self):
        status = get_api_rate_limit_status()
        assert isinstance(status, list)

    def test_rate_limit_decorator_requires_flask(self):
        def dummy_view():
            return "ok"

        decorated = rate_limit(limit=(100, 60), key_prefix="test")(dummy_view)

        from app import app

        with app.test_request_context("/test"):
            resp = decorated()
            assert resp == "ok"
