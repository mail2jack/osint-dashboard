"""
Template smoke tests — verify every template compiles without Jinja2 syntax errors.
This catches missing {% endif %}, {% endfor %}, and other nesting bugs.
"""

import os


def test_all_templates_compile(app):
    """Every template in templates/cms/ compiles without Jinja2 syntax error."""
    errors = []
    compiled = 0

    for searchpath in app.jinja_loader.searchpath:
        if not os.path.isdir(searchpath):
            continue
        for root, _dirs, files in os.walk(searchpath):
            for f in files:
                if not f.endswith('.html'):
                    continue
                rel = os.path.relpath(os.path.join(root, f), searchpath)
                try:
                    app.jinja_env.get_template(rel)
                    compiled += 1
                except Exception as e:
                    errors.append(f'{rel}: {e}')

    assert errors == [], \
        f'{len(errors)} template(s) failed to compile:\n' + '\n'.join(errors)
    assert compiled > 0, 'No templates found to compile'
