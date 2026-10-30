"""富文本白名单过滤（文档 5.4 "富文本白名单过滤"）。"""
from __future__ import annotations

import re
from html.parser import HTMLParser

ALLOWED_TAGS = {
    "p", "br", "strong", "b", "em", "i", "u", "s", "ul", "ol", "li",
    "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "a", "img",
    "span", "div", "table", "thead", "tbody", "tr", "td", "th",
    "code", "pre", "hr", "figure", "figcaption",
}
VOID_TAGS = {"br", "img", "hr"}
ALLOWED_ATTRS = {"href", "src", "alt", "title", "target", "rel", "colspan", "rowspan"}
SAFE_URL = re.compile(r"^(https?:|/|#|mailto:tel:)", re.IGNORECASE)


class _Sanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in ("script", "style", "iframe", "object", "embed"):
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag not in ALLOWED_TAGS:
            return
        safe_attrs = []
        for name, value in attrs:
            lname = name.lower()
            if lname.startswith("on") or lname not in ALLOWED_ATTRS or value is None:
                continue
            if lname in ("href", "src") and not SAFE_URL.match(value.strip()):
                continue
            safe_attrs.append(f' {lname}="{_escape_attr(value)}"')
        self.out.append(f"<{tag}{''.join(safe_attrs)}>")

    def handle_startendtag(self, tag: str, attrs) -> None:
        if self._skip_depth:
            return
        if tag in ALLOWED_TAGS:
            self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "iframe", "object", "embed"):
            self._skip_depth = max(self._skip_depth - 1, 0)
            return
        if self._skip_depth or tag not in ALLOWED_TAGS or tag in VOID_TAGS:
            return
        self.out.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        self.out.append(_escape_text(data))


def _escape_text(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _escape_attr(value: str) -> str:
    return _escape_text(value).replace('"', "&quot;")


def sanitize_html(value: str | None, max_length: int | None = None) -> str | None:
    """按白名单清理富文本；超长截断。"""
    if not value:
        return value
    parser = _Sanitizer()
    try:
        parser.feed(value)
        parser.close()
        cleaned = "".join(parser.out)
    except Exception:  # pragma: no cover - 解析异常时降级为纯文本
        cleaned = _escape_text(value)
    if max_length and len(cleaned) > max_length:
        cleaned = cleaned[:max_length]
    return cleaned


def plain_text_length(value: str | None) -> int:
    if not value:
        return 0
    return len(re.sub(r"<[^>]+>", "", value))
