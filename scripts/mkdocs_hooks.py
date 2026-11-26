"""Rewrite links that leave docs/ (../examples/x.py) to GitHub, so existing docs stay untouched."""

import re

BASE = "https://github.com/jmmana/sqlguardllm/blob/main/"
LINK = re.compile(r"\]\(\.\./([^)#\s]+)(#[^)\s]*)?\)")


def on_page_markdown(markdown, page, config, files):
    if page.file.src_uri.startswith("es/"):
        return markdown
    return LINK.sub(lambda m: f"]({BASE}{m.group(1)}{m.group(2) or ''})", markdown)
