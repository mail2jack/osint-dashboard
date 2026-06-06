#!/usr/bin/env python3
"""Replace known inline style patterns with CSS utility classes."""

import re
import os

# Mapping from normalized style string → CSS class name(s)
# Order matters: more specific patterns first
STYLE_MAP: list[tuple[str, str]] = [
    # Complex multi-property patterns first
    (
        "display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem",
        "flex-between mb-1",
    ),
    (
        "display: flex; justify-content: space-between; align-items: center",
        "flex-between",
    ),
    (
        "display:grid; grid-template-columns:1fr 1fr; gap:0.5rem; font-size:0.85rem",
        "grid-2-sm",
    ),
    ("display:grid; grid-template-columns:1fr 1fr; gap:0.5rem", "grid-2"),
    ("display: flex; gap: 0.5rem", "flex-gap"),
    ("display: flex; flex-wrap: wrap; gap: 0.5rem", "flex-wrap"),
    ("color:#666; font-size:0.85rem", "text-muted-sm"),
    ("color: #666; font-size: 0.875rem", "text-muted-sm"),
    (
        "display:none; grid-column:1/-1; margin-top:0.25rem",
        "hidden col-span-full mt-025",
    ),
    ("width: 100%; padding: 0.5rem", "w100-p5"),
    ("width:100%; padding:0.5rem", "w100-p5"),
    (
        "font-size:0.7rem; color:var(--text-secondary); text-transform:uppercase",
        "text-xs-upper",
    ),
    (
        "background: #f5f5f5; padding: 1rem; border-radius: 8px; text-align: center",
        "card-center",
    ),
    ("text-align: center; padding: 2rem", "text-center p-2"),
    (
        "margin-top: 1rem; display: flex; gap: 0.5rem; justify-content: center",
        "mt-1 flex-gap justify-center",
    ),
    ("display: flex; gap: 0.5rem; margin-top: 1rem", "flex-gap mt-1"),
    ("font-size:0.8rem; color:#2563eb", "text-sm text-link"),
    (
        "display:flex; justify-content:space-between; align-items:flex-start",
        "flex items-start",
    ),
    # Single property patterns
    ("color: #dc2626", "text-red"),
    ("color: #666", "text-muted"),
    ("color: #0ea5e9", "text-blue"),
    ("color: #8b5cf6", "text-purple"),
    ("color: #f59e0b", "text-amber"),
    ("background: #059669", "bg-green"),
    ("background:#059669", "bg-green"),
    ("background: #7c3aed", "bg-purple"),
    ("background: #666", "bg-gray"),
    ("background: #8b5cf6", "text-purple"),
    ("background: #ede9fe", "bg-purple-light"),
    ("background: #f5f5f5", "bg-light-gray"),
    ("margin-bottom: 1rem", "mb-1"),
    ("margin-bottom: 0.5rem", "mb-05"),
    ("margin-bottom: 2rem", "mb-2"),
    ("margin-top: 1rem", "mt-1"),
    ("margin-top:0.25rem", "mt-025"),
    ("margin-top: 0.5rem", "mt-05"),
    ("margin-top: 2rem", "mt-2"),
    ("margin: 0", "m-0"),
    ("grid-column:1/-1", "col-span-full"),
    ("text-align:center", "text-center"),
    ("text-align: center", "text-center"),
    ("display: none", "hidden"),
    ("display: inline-block", "inline-block"),
    ("display: inline", "inline"),
    ("display:block", "block"),
    ("display: flex", "flex"),
    ("padding: 0.5rem", "p-05"),
    ("padding: 0.5rem 1rem", "p-05-1"),
    ("padding: 1rem", "p-1"),
    ("max-width: 400px", "max-w-400"),
    (
        "font-size: 1.5rem; cursor: pointer; color: var(--text-secondary)",
        "text-lg cursor-pointer text-secondary",
    ),
    ("font-size: 1.5rem", "text-lg"),
    ("font-size:0.85rem", "text-sm"),
    ("font-size: 0.875rem", "text-sm"),
    ("text-transform:uppercase", "text-uppercase"),
    ("cursor: pointer", "cursor-pointer"),
]


def normalize(s: str) -> str:
    s = s.strip()
    s = re.sub(r"\s*;\s*", "; ", s)
    s = re.sub(r"\s*:\s*", ": ", s)
    s = s.strip("; ")
    s = s.rstrip(";")
    return s


def replace_inline_styles(text: str) -> tuple[str, int]:
    replacements = 0

    def _replacer(match):
        nonlocal replacements
        full_attr = match.group(0)
        style_val = match.group(1)
        nv = normalize(style_val)

        # Try to match the normalized style value
        for pattern, class_name in STYLE_MAP:
            if nv == pattern:
                replacements += 1
                new_attr = f'class="{class_name}"'
                # Check if element already has a class attribute
                before = match.string[: match.start()]
                # Find the opening tag start
                tag_start = before.rfind("<")
                tag_text = match.string[tag_start : match.start()]
                if 'class="' in tag_text or "class='" in tag_text:
                    # Element already has class – append
                    return f' class="{class_name}"'
                return new_attr

        return full_attr

    result = re.sub(r'style="([^"]*)"', _replacer, text)
    return result, replacements


def main():
    root = os.path.join(os.path.dirname(__file__), "..")
    files = [
        "templates/cms/subjects/view.html",
        "templates/cms/cases/view.html",
    ]

    total_replaced = 0
    total_remaining = 0

    for fname in files:
        path = os.path.join(root, fname)
        with open(path) as f:
            orig = f.read()

        modified, count = replace_inline_styles(orig)
        remaining = len(re.findall(r'style="([^"]*)"', modified))

        with open(path, "w") as f:
            f.write(modified)

        total_replaced += count
        total_remaining += remaining
        print(f"{fname}: {count} replaced, {remaining} remaining")

    print(
        f"\nTotal: {total_replaced} replaced, {total_remaining} remaining inline styles"
    )


if __name__ == "__main__":
    main()
