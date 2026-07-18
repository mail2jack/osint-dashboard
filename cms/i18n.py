import os

from flask_babel import Babel
from flask import request, session

babel = Babel()


def get_locale():
    lang = session.get("lang")
    if lang in ("nl", "en", "de", "fr"):
        return lang
    return request.accept_languages.best_match(["en", "nl", "de", "fr"], default="en")


def init_i18n(app):
    app.config.setdefault("BABEL_DEFAULT_LOCALE", "en")
    app.config.setdefault("BABEL_DEFAULT_TIMEZONE", "Europe/Amsterdam")
    app.config.setdefault(
        "BABEL_TRANSLATION_DIRECTORIES",
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "translations"),
    )
    babel.init_app(app, locale_selector=get_locale)
