"""チャットリンク化と同等のURL検出ユーティリティ。"""

from __future__ import annotations

import re
from urllib.parse import urlparse

MAX_URL_LENGTH = 2048
MAX_RELATIVE_LENGTH = 200

_URL_CANDIDATE_RE = re.compile(
    r"https?://[^\s<]+"
    r"|www\.[^\s<]+"
    r"|/(?:ja|en)/[A-Za-z0-9\-._/?=&%#+]+"
    r"|\b(?:(?:[a-z0-9-]+\.)*)(?:brawlinsights\.com|brawlstars\.com|x\.com|twitter\.com|"
    r"discord\.gg|discord\.com|youtube\.com|youtu\.be|tiktok\.com)"
    r"(?:/[A-Za-z0-9\-._/?=&%#+]*)?",
    re.IGNORECASE,
)

_TRAILING_SENTENCE_PUNCT_RE = re.compile(r"[.,;:!?。、』」】>]$")


def _strip_trailing_url_punctuation(raw_url: str) -> str:
    url = raw_url
    while _TRAILING_SENTENCE_PUNCT_RE.search(url):
        url = url[:-1]
    while url and url[-1] in ")]}":
        closing = url[-1]
        opening = {")": "(", "]": "[", "}": "{"}[closing]
        if url.count(closing) > url.count(opening):
            url = url[:-1]
        else:
            break
    return url


def _host_matches_domain(hostname: str, domain: str) -> bool:
    host = (hostname or "").lower().rstrip(".")
    base = (domain or "").lower()
    return host == base or host.endswith(f".{base}")


def _is_internal_hostname(hostname: str) -> bool:
    return _host_matches_domain(hostname, "brawlinsights.com")


def _is_allowed_bare_hostname(hostname: str) -> bool:
    host = (hostname or "").lower().rstrip(".")
    if not host:
        return False
    if _is_internal_hostname(host):
        return True
    if _host_matches_domain(host, "brawlstars.com"):
        return True
    if _host_matches_domain(host, "x.com"):
        return True
    if _host_matches_domain(host, "twitter.com"):
        return True
    if _host_matches_domain(host, "discord.gg"):
        return True
    if _host_matches_domain(host, "discord.com"):
        return True
    if _host_matches_domain(host, "youtube.com"):
        return True
    if host == "youtu.be":
        return True
    if _host_matches_domain(host, "tiktok.com"):
        return True
    return False


def _is_valid_relative_site_path(path: str) -> bool:
    if not re.match(r"^/(?:ja|en)/", path):
        return False
    if len(path) > MAX_RELATIVE_LENGTH:
        return False
    return bool(re.match(r"^/(?:ja|en)/[A-Za-z0-9\-._/?=&%#+]*$", path))


def _resolve_link_from_match(matched_text: str) -> str | None:
    trimmed = _strip_trailing_url_punctuation(matched_text)
    if not trimmed:
        return None

    if re.match(r"^/(?:ja|en)/", trimmed):
        if not _is_valid_relative_site_path(trimmed):
            return None
        return trimmed

    href = trimmed
    if re.match(r"^www\.", trimmed, flags=re.IGNORECASE):
        href = f"https://{trimmed}"
    elif not re.match(r"^https?://", trimmed, flags=re.IGNORECASE):
        href = f"https://{trimmed}"

    if len(href) > MAX_URL_LENGTH:
        return None

    parsed = urlparse(href)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None

    hostname = parsed.hostname.lower()
    if not re.match(r"^https?://", trimmed, flags=re.IGNORECASE) and not re.match(
        r"^www\.", trimmed, flags=re.IGNORECASE
    ):
        if not _is_allowed_bare_hostname(hostname):
            return None

    return trimmed


def text_contains_detected_url(text: str | None) -> bool:
    """テキストに検出対象URLが1つ以上含まれるか。"""
    if not text or not isinstance(text, str):
        return False

    for match in _URL_CANDIDATE_RE.finditer(text):
        if _resolve_link_from_match(match.group(0)):
            return True
    return False
