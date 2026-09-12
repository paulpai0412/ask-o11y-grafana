"""Static-content safety for native Grafana text panels; no report contract."""
from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

PLOTLY_PLUGIN_ID = "asko11y-plotly-panel"


class _NarrativeHTML(HTMLParser):
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in {"div", "p", "span", "h1", "h2", "h3", "h4", "h5", "h6", "strong", "b", "em", "i", "ul", "ol", "li", "br", "hr", "blockquote", "code", "pre", "table", "thead", "tbody", "tr", "td", "th"}:
            raise ValueError("text panels are narrative-only; images require the Plotly plugin")
        for name, value in attrs:
            if name == "style":
                if not re.fullmatch(r"[A-Za-z0-9\s:;#.,%+\-]*", value or ""):
                    raise ValueError("text panel style contains an unsafe image or active binding")
            elif name not in {"class", "title", "role", "aria-label", "lang", "dir", "colspan", "rowspan"}:
                raise ValueError("text panel attribute is not narrative-only")


def validate_narrative_content(content: Any) -> None:
    if not isinstance(content, str) or "![" in content:
        raise ValueError("text panels are narrative-only; images require the Plotly plugin")
    parser = _NarrativeHTML(convert_charrefs=True)
    parser.feed(content)
    parser.close()
