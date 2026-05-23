from ..legacy_routes import cms_bp

def register_modules():
    from . import dashboard
    from . import lookups
    from . import social
    from . import financials
    from . import findings
    from . import comments
    from . import settings
    from . import exports
    from . import documents
    from . import reminders
    from . import audit
    from . import system
    from . import search
    from . import templates
    from . import screenshots
    from . import clients
    from . import cases
    from . import osint_search
    from . import subjects
    from . import spiderfoot
