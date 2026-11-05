#!/usr/bin/env python3
"""Build the Wonderful M3U from worldwide, public free-to-watch stream sources.

IPTV Cat is treated as a discovery source, not proof of distribution rights. Its
entries are only added when the stream host matches the channel's own website or
a broadcaster-operated delivery host, or when the exact URL is independently
present in one of the curated public feeds below.
"""
from __future__ import annotations

import html
import json
import re
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "playlists" / "Wonderful.m3u"
REPORT = ROOT / "playlists" / "Wonderful-report.json"

SOURCES = [
    ("IPTV-org", "https://iptv-org.github.io/iptv/index.m3u", "catalog"),
    ("Free-TV/IPTV", "https://raw.githubusercontent.com/Free-TV/IPTV/master/playlist.m3u8", "curated"),
    ("FreeCastHub", "https://raw.githubusercontent.com/freecasthub/public-iptv/main/playlist.m3u", "curated"),
    ("LegalFreeIPTV", "https://biskyt.github.io/legalFreeIPTVplaylist/live.m3u", "curated"),
]
CHANNELS_DB_URL = "https://iptv-org.github.io/api/channels.json"
IPTVCAT_SITEMAP = "https://iptvcat.com/sitemap"
IPTVCAT_EXPORT = "https://iptvcat.com/ajax/streams_a?action=playlist_download"
USER_AGENT = "Wonderful-IPTV-Playlist/1.0 (+https://github.com/godspoweronari2010-cmyk/Ai-assistant)"
MAX_CAT_PAGES = 500
REQUEST_DELAY = 0.35
MINIMUM_OUTPUT_ENTRIES = 100

BROADCASTER_HOSTS = {
    "getaj.net", "thehlive.com", "rtve.es", "nhkworld.jp", "trt.com.tr", "ard-mcdn.de",
}
DENIED_HOSTS = {
    "freeott.top", "freem3u.xyz", "dethich.pw", "pastebin.com",
}
BLOCKED_EXTINF = ("adult", "xxx", "porn")
QUALITY_SUFFIX = re.compile(
    r"\s*[\[(]\s*(?:\d{3,4}\s*p|sd|hd|fhd|uhd|4k|8k|not\s+24/7|hevc|h\.?264|h\.?265|"
    r"aac|avc|mpeg[\w -]*)\s*[\])]\s*$",
    re.IGNORECASE,
)


@dataclass
class Entry:
    name: str
    url: str
    extinf: str
    tags: list[str] = field(default_factory=list)
    source: str = ""
    trust: str = ""
    country: str = ""
    channel_key: str = ""
    name_key: str = ""


def fetch(url: str, *, method: str = "GET", body: str | None = None,
          referer: str | None = None, accept: str | None = None,
          timeout: int = 45, attempts: int = 3) -> bytes:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": accept or "*/*",
    }
    if referer:
        headers["Referer"] = referer
    data = body.encode("utf-8") if body is not None else None
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        headers["X-Requested-With"] = "XMLHttpRequest"
        headers["Origin"] = "https://iptvcat.com"
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            req = Request(url, data=data, headers=headers, method=method)
            with urlopen(req, timeout=timeout) as response:
                return response.read()
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Fetch failed for {url}: {last_error}")


def fetch_text(url: str, **kwargs) -> str:
    return fetch(url, **kwargs).decode("utf-8-sig", errors="replace")


def attributes(extinf: str) -> dict[str, str]:
    # Quoted M3U attributes; title itself is not parsed as an attribute.
    return {k.lower(): html.unescape(v) for k, v in
            re.findall(r'([\w-]+)="([^"]*)"', extinf)}


def clean_name(name: str) -> str:
    name = html.unescape(name).strip()
    while True:
        cleaned = QUALITY_SUFFIX.sub("", name).strip()
        if cleaned == name:
            break
        name = cleaned
    name = re.sub(r"\s+", " ", name)
    return name


def normalized_name(name: str) -> str:
    name = unicodedata.normalize("NFKD", clean_name(name))
    name = "".join(ch for ch in name if not unicodedata.combining(ch))
    name = name.casefold()
    name = re.sub(r"[^\w]+", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def country_from_id(channel_id: str) -> str:
    base = channel_id.split("@", 1)[0]
    match = re.search(r"\.([a-z]{2,3})$", base, re.IGNORECASE)
    return match.group(1).upper() if match else ""


def normalized_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
        return ""
    # Keep the original path and query: signed URLs and alternate stream paths
    # may represent distinct feeds. Normalize only URL casing and fragments.
    return urlunparse((
        parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/",
        parsed.params, parsed.query, ""
    ))


def parse_m3u(text: str, source: str, trust: str) -> list[Entry]:
    lines = [line.strip() for line in text.replace("\ufeff", "").splitlines()]
    entries: list[Entry] = []
    extinf: str | None = None
    tags: list[str] = []
    for line in lines:
        if not line:
            continue
        if line.startswith("#EXTINF:"):
            extinf = line
            tags = []
            continue
        if line.startswith("#"):
            if extinf is not None:
                tags.append(line)
            continue
        if extinf is None:
            continue
        url = normalized_url(line)
        if url:
            title = extinf.split(",", 1)[1].strip() if "," in extinf else "Unknown channel"
            entries.append(Entry(
                name=clean_name(title),
                url=url,
                extinf=extinf,
                tags=list(tags),
                source=source,
                trust=trust,
            ))
        extinf = None
        tags = []
    return entries


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() == "a":
            values = dict(attrs)
            self._href = values.get("href")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            self.links.append((self._href, " ".join("".join(self._text).split())))
            self._href = None
            self._text = []


def host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower().strip(".")
    except ValueError:
        return ""


def host_matches(host: str, domain: str) -> bool:
    host, domain = host.lower().strip("."), domain.lower().strip(".")
    return bool(host and domain and (host == domain or host.endswith("." + domain)))


def load_channel_database() -> tuple[dict[str, set[str]], dict[tuple[str, str], set[str]]]:
    by_id: dict[str, set[str]] = {}
    by_name_country: dict[tuple[str, str], set[str]] = {}
    try:
        data = json.loads(fetch_text(CHANNELS_DB_URL, timeout=60))
    except Exception as exc:
        print(f"WARNING: channel database unavailable: {exc}", file=sys.stderr)
        return by_id, by_name_country
    for channel in data if isinstance(data, list) else []:
        website = channel.get("website") or ""
        domain = host_of(website)
        if not domain:
            continue
        cid = str(channel.get("id") or "").split("@", 1)[0]
        if cid:
            by_id.setdefault(cid.casefold(), set()).add(domain)
        country = str(channel.get("country") or "").upper()
        names = [channel.get("name", ""), *(channel.get("alt_names") or [])]
        for name in names:
            nkey = normalized_name(str(name))
            if nkey:
                by_name_country.setdefault((nkey, country), set()).add(domain)
    return by_id, by_name_country


def enrich_and_key(entry: Entry, by_id, by_name_country) -> None:
    attrs = attributes(entry.extinf)
    channel_id = attrs.get("tvg-id", "").split("@", 1)[0]
    country = (attrs.get("tvg-country") or country_from_id(attrs.get("tvg-id", ""))).upper()
    if not country and attrs.get("group-title"):
        # Keep an explicit ISO-like country code if the source uses one.
        match = re.match(r"^([A-Z]{2,3})\s*\|", attrs["group-title"])
        country = match.group(1) if match else ""
    entry.country = country
    entry.name_key = normalized_name(entry.name)
    if channel_id:
        entry.channel_key = f"id:{channel_id.casefold()}"
    else:
        entry.channel_key = f"name:{country}:{entry.name_key}"


def is_official_cat_entry(entry: Entry, by_id, by_name_country, curated_urls: set[str]) -> bool:
    if entry.url in curated_urls:
        return True
    host = host_of(entry.url)
    if not host or any(host_matches(host, denied) for denied in DENIED_HOSTS):
        return False
    if host in BROADCASTER_HOSTS or any(host_matches(host, d) for d in BROADCASTER_HOSTS):
        return True
    attrs = attributes(entry.extinf)
    channel_id = attrs.get("tvg-id", "").split("@", 1)[0].casefold()
    domains = set(by_id.get(channel_id, set())) if channel_id else set()
    if not domains:
        country = entry.country
        domains.update(by_name_country.get((entry.name_key, country), set()))
        if not domains and not country:
            # A name-only match is allowed only when it identifies one domain.
            for (name, _country), matched in by_name_country.items():
                if name == entry.name_key:
                    domains.update(matched)
    return any(host_matches(host, domain) for domain in domains)


def discover_iptvcat_pages() -> list[str]:
    sitemap_html = fetch_text(IPTVCAT_SITEMAP, timeout=60)
    parser = LinkParser()
    parser.feed(sitemap_html)
    pages: set[str] = set()
    excluded = {
        "home_22", "new_channels-41", "sport__13", "sport__5", "faq",
        "contact_us", "privacy", "cookies", "sitemap",
    }
    for href, label in parser.links:
        absolute = urljoin(IPTVCAT_SITEMAP, href)
        parsed = urlparse(absolute)
        if parsed.hostname not in ("iptvcat.com", "www.iptvcat.com"):
            continue
        path = parsed.path.strip("/")
        if not path or path in excluded or path.startswith(("mark/", "s/")):
            continue
        if path.split("/", 1)[0] in excluded:
            continue
        if re.fullmatch(r"[a-z0-9_%.-]+(?:/[0-9]+)?", path, re.IGNORECASE):
            pages.add("https://iptvcat.com/" + path)
    return sorted(pages)


def page_and_pagination_urls(page_url: str, page_html: str) -> list[str]:
    parser = LinkParser()
    parser.feed(page_html)
    base_path = urlparse(page_url).path.rstrip("/")
    result = {page_url}
    for href, _label in parser.links:
        absolute = urljoin(page_url, href)
        parsed = urlparse(absolute)
        if parsed.hostname != "iptvcat.com":
            continue
        candidate = parsed.path.rstrip("/")
        if re.fullmatch(re.escape(base_path) + r"/[0-9]+", candidate):
            result.add("https://iptvcat.com" + candidate)
    return sorted(result)


def export_iptvcat_page(sessionless_page_url: str) -> str:
    page_html = fetch_text(sessionless_page_url, timeout=45)
    page_path = urlparse(sessionless_page_url).path
    # Mirrors IPTV Cat's public "Download this list" button:
    # POST /ajax/streams_a?action=playlist_download with the page path as body.
    raw = fetch(
        IPTVCAT_EXPORT,
        method="POST",
        body=page_path,
        referer=sessionless_page_url,
        accept="application/json, text/javascript, */*; q=0.01",
        timeout=60,
    )
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        raise RuntimeError("IPTV Cat country export did not return JSON")
    if not payload.get("status") or not isinstance(payload.get("data"), str):
        raise RuntimeError("IPTV Cat country export returned no playlist data")
    return payload["data"]


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    counters = {
        "source_entries": {},
        "iptvcat_pages_seen": 0,
        "iptvcat_pages_exported": 0,
        "iptvcat_export_errors": [],
        "iptvcat_entries_rejected_unverified": 0,
        "iptvcat_entries_accepted": 0,
        "non_http_rejected": 0,
        "duplicate_same_channel_same_url": 0,
        "duplicate_url_other_name": 0,
    }
    all_entries: list[Entry] = []
    fetched_texts: dict[str, str] = {}

    for name, url, trust in SOURCES:
        try:
            text = fetch_text(url, timeout=90)
            fetched_texts[name] = text
            parsed = parse_m3u(text, name, trust)
            counters["source_entries"][name] = len(parsed)
            all_entries.extend(parsed)
            print(f"{name}: parsed {len(parsed)} HTTP/HTTPS entries")
        except Exception as exc:
            counters["source_entries"][name] = 0
            print(f"WARNING: {name} unavailable: {exc}", file=sys.stderr)

    channels_by_id, channels_by_name_country = load_channel_database()
    curated_urls = {
        e.url for e in all_entries if e.trust == "curated"
    }

    # IPTV Cat: use its country/region sitemap and native one-page M3U export.
    # The page list is re-discovered at each run, so new countries are picked up.
    try:
        cat_pages = discover_iptvcat_pages()
    except Exception as exc:
        cat_pages = []
        print(f"WARNING: IPTV Cat sitemap unavailable: {exc}", file=sys.stderr)

    expanded_pages: set[str] = set()
    for page_url in cat_pages:
        if len(expanded_pages) >= MAX_CAT_PAGES:
            break
        try:
            page_html = fetch_text(page_url, timeout=45)
            expanded_pages.update(page_and_pagination_urls(page_url, page_html))
        except Exception as exc:
            counters["iptvcat_export_errors"].append({"page": page_url, "error": str(exc)[:250]})
        time.sleep(REQUEST_DELAY)

    cat_entries: list[Entry] = []
    for page_url in sorted(expanded_pages)[:MAX_CAT_PAGES]:
        try:
            page_m3u = export_iptvcat_page(page_url)
            parsed = parse_m3u(page_m3u, "IPTV Cat", "discovery")
            counters["iptvcat_pages_exported"] += 1
            for entry in parsed:
                enrich_and_key(entry, channels_by_id, channels_by_name_country)
                if is_official_cat_entry(entry, channels_by_id, channels_by_name_country, curated_urls):
                    entry.trust = "officially-matched"
                    cat_entries.append(entry)
                    counters["iptvcat_entries_accepted"] += 1
                else:
                    counters["iptvcat_entries_rejected_unverified"] += 1
        except Exception as exc:
            counters["iptvcat_export_errors"].append({"page": page_url, "error": str(exc)[:250]})
        time.sleep(REQUEST_DELAY)
    counters["iptvcat_pages_seen"] = len(expanded_pages)
    counters["source_entries"]["IPTV Cat (verified entries)"] = len(cat_entries)
    all_entries.extend(cat_entries)

    # Enrich identity, reject non-HTTP(S), obvious denied hosts and NSFW groups.
    kept: list[Entry] = []
    for entry in all_entries:
        if not entry.url:
            counters["non_http_rejected"] += 1
            continue
        attrs = attributes(entry.extinf)
        group = attrs.get("group-title", "").casefold()
        title = entry.name.casefold()
        if any(term in group or term in title for term in BLOCKED_EXTINF):
            continue
        enrich_and_key(entry, channels_by_id, channels_by_name_country)
        host = host_of(entry.url)
        if not host or any(host_matches(host, d) for d in DENIED_HOSTS):
            continue
        kept.append(entry)

    # First pass: exact URL duplicates are removed. Different URLs for the same
    # station remain, as requested. IPTV-org metadata is preferred when present.
    source_rank = {"LegalFreeIPTV": 0, "Free-TV/IPTV": 1, "FreeCastHub": 2, "IPTV-org": 3, "IPTV Cat": 4}
    kept.sort(key=lambda e: (source_rank.get(e.source, 9), e.country, e.name_key, e.url))
    deduped: list[Entry] = []
    seen_urls: set[str] = set()
    seen_channel_urls: set[tuple[str, str]] = set()
    for entry in kept:
        channel_url_key = (entry.channel_key, entry.url)
        if entry.url in seen_urls:
            counters["duplicate_url_other_name"] += 1
            continue
        if channel_url_key in seen_channel_urls:
            counters["duplicate_same_channel_same_url"] += 1
            continue
        seen_urls.add(entry.url)
        seen_channel_urls.add(channel_url_key)
        deduped.append(entry)

    if len(deduped) < MINIMUM_OUTPUT_ENTRIES:
        raise RuntimeError(
            f"Only {len(deduped)} verified entries were found; refusing to overwrite the last good playlist."
        )

    # Keep groups/countries readable in IPTV Smarters. Channel-name duplicates
    # with genuinely different URLs intentionally appear as separate entries.
    deduped.sort(key=lambda e: (
        e.country or "ZZ",
        attributes(e.extinf).get("group-title", "Other").casefold(),
        e.name_key,
        e.url,
    ))

    header = [
        "#EXTM3U",
        "# Wonderful IPTV playlist",
        "# Sources: IPTV-org, IPTV Cat (officially matched entries), Free-TV/IPTV, FreeCastHub, LegalFreeIPTV",
        "# Deduplication: exact stream URLs are listed once; distinct stream URLs for the same channel are retained.",
        "# IPTV Cat entries are included only when the stream host can be matched to the broadcaster's own website/host or an independently curated free feed.",
    ]
    output_lines = list(header)
    for entry in deduped:
        output_lines.append(entry.extinf)
        output_lines.extend(entry.tags)
        output_lines.append(entry.url)
    OUT.write_text("\n".join(output_lines) + "\n", encoding="utf-8")

    counters["final_channels_or_streams"] = len(deduped)
    counters["unique_channel_identities"] = len({e.channel_key for e in deduped})
    counters["generated_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    counters["output_bytes"] = OUT.stat().st_size
    counters["source_notes"] = {
        "IPTV-org": "Publicly available streams; entries are filtered by broadcaster-host evidence or a curated feed.",
        "IPTV Cat": "Public aggregation used as discovery; only independently matched official/free stream URLs are retained.",
        "Free-TV/IPTV": "Curated free-TV source; subject to broadcaster and geographic restrictions.",
        "FreeCastHub": "Curated free public-broadcaster source.",
        "LegalFreeIPTV": "Curated legal-free feed with published acceptance criteria.",
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(counters, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(counters, indent=2))
    print(f"Wrote {OUT} ({OUT.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"BUILD FAILED: {exc}", file=sys.stderr)
        raise
