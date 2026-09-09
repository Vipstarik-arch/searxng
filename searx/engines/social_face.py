# SPDX-License-Identifier: AGPL-3.0-or-later
"""Find people on popular social networks.

Two query modes:

- **Photo**: paste a public image URL (``https://…/photo.jpg``) or a
  ``data:image/…;base64,…`` URI.  The engine runs TinEye reverse-image search
  and keeps only hits that live on social networks.
- **Name**: any other query is treated as a person name.  Bing is queried with
  ``site:`` filters for the same networks.

Only publicly indexed pages are returned.  Private profiles, logins and
CAPTCHA walls are out of scope.

Shortcut: ``!sface``.
"""

from __future__ import annotations

import base64
import logging
import re
import typing as t
from datetime import datetime
from urllib.parse import parse_qs, urlencode, urlparse

from lxml import html

from searx.utils import eval_xpath, eval_xpath_getindex, eval_xpath_list, extract_text

if t.TYPE_CHECKING:
    from searx.extended_types import SXNG_Response
    from searx.search.processors import OnlineParams

logger = logging.getLogger("searx.engines.social_face")

about = {
    "website": "https://tineye.com",
    "wikidata_id": "Q2382535",
    "official_api_documentation": "https://api.tineye.com/python/docs/",
    "use_official_api": False,
    "require_api_key": False,
    "results": "JSON",
}

categories = ["social media", "images"]
paging = True
safesearch = False

tineye_base_url = "https://tineye.com"
tineye_search_path = "/api/v1/result_json/?page={page}&{query}"
bing_base_url = "https://www.bing.com/search"

# Registrable hosts → display name.  Subdomains (m.vk.com, www.…) match too.
SOCIAL_NETWORKS: dict[str, str] = {
    "vk.com": "VK",
    "vk.ru": "VK",
    "instagram.com": "Instagram",
    "facebook.com": "Facebook",
    "fb.com": "Facebook",
    "fb.watch": "Facebook",
    "ok.ru": "Odnoklassniki",
    "odnoklassniki.ru": "Odnoklassniki",
    "tiktok.com": "TikTok",
    "x.com": "X",
    "twitter.com": "X",
    "linkedin.com": "LinkedIn",
    "t.me": "Telegram",
    "telegram.me": "Telegram",
    "telegram.org": "Telegram",
    "youtube.com": "YouTube",
    "youtu.be": "YouTube",
    "threads.net": "Threads",
    "pinterest.com": "Pinterest",
    "pin.it": "Pinterest",
    "reddit.com": "Reddit",
    "redd.it": "Reddit",
    "twitch.tv": "Twitch",
    "snapchat.com": "Snapchat",
    "tumblr.com": "Tumblr",
    "flickr.com": "Flickr",
    "bsky.app": "Bluesky",
    "weibo.com": "Weibo",
    "likee.video": "Likee",
    "mastodon.social": "Mastodon",
    "truthsocial.com": "Truth Social",
    "discord.com": "Discord",
    "soundcloud.com": "SoundCloud",
}

# Kept short so Bing does not truncate the query.
SITE_FILTER_DOMAINS: tuple[str, ...] = (
    "vk.com",
    "instagram.com",
    "facebook.com",
    "ok.ru",
    "tiktok.com",
    "x.com",
    "twitter.com",
    "linkedin.com",
    "t.me",
    "youtube.com",
    "threads.net",
    "pinterest.com",
    "reddit.com",
    "twitch.tv",
    "snapchat.com",
)

_IMAGE_URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
_DATA_IMAGE_RE = re.compile(r"data:image/[^;\s]*;base64,[^\s]+", re.IGNORECASE)
_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".avif")


def _host(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def match_social_network(url: str) -> str | None:
    """Return the social network name for ``url``, or ``None``."""
    host = _host(url)
    if not host:
        return None
    if host in SOCIAL_NETWORKS:
        return SOCIAL_NETWORKS[host]
    for domain, name in SOCIAL_NETWORKS.items():
        if host.endswith("." + domain):
            return name
    return None


def extract_image_url(query: str) -> str | None:
    """Return a ``data:image`` URI or http(s) URL from ``query``, if any."""
    query = (query or "").strip()
    data = _DATA_IMAGE_RE.search(query)
    if data:
        return data.group(0)
    http_url = _IMAGE_URL_RE.search(query)
    if http_url:
        return http_url.group(0)
    return None


def is_image_query(query: str) -> bool:
    """True when the query is a reverse-image lookup (URL or data URI)."""
    query = (query or "").strip()
    if not query:
        return False
    if _DATA_IMAGE_RE.search(query):
        return True
    match = _IMAGE_URL_RE.match(query)
    if not match:
        return False
    url = match.group(0)
    path = urlparse(url).path.lower()
    if any(path.endswith(ext) for ext in _IMAGE_EXTS):
        return True
    # A query that is only a URL is treated as a photo to reverse-search.
    return query == url


def site_restricted_query(name: str) -> str:
    """Build a Bing query restricted to popular social networks."""
    sites = " OR ".join(f"site:{domain}" for domain in SITE_FILTER_DOMAINS)
    return f"{name.strip()} ({sites})"


def decode_bing_href(href: str) -> str:
    """Unwrap Bing click-tracking URLs when present."""
    if not href.startswith("https://www.bing.com/ck/a?"):
        return href
    qs = parse_qs(urlparse(href).query)
    u_values = qs.get("u")
    if not u_values:
        return href
    u_val = u_values[0]
    if not u_val.startswith("a1"):
        return href
    encoded = u_val[2:]
    encoded += "=" * (-len(encoded) % 4)
    try:
        return base64.urlsafe_b64decode(encoded).decode("utf-8", errors="replace")
    except (ValueError, UnicodeDecodeError):
        return href


def _tineye_request(image_url: str, params: "OnlineParams") -> "OnlineParams":
    params["raise_for_httperror"] = False
    query = urlencode({"url": image_url})
    params["url"] = tineye_base_url + tineye_search_path.format(query=query, page=params["pageno"])
    params["headers"].update(
        {
            "Connection": "keep-alive",
            "Host": "tineye.com",
            "DNT": "1",
            "TE": "trailers",
        }
    )
    return params


def _bing_request(name: str, params: "OnlineParams") -> "OnlineParams":
    query_params: dict[str, str | int] = {
        "q": site_restricted_query(name),
        "first": (int(params.get("pageno", 1)) - 1) * 10 + 1,
    }
    params["url"] = f"{bing_base_url}?{urlencode(query_params)}"
    return params


def request(query: str, params: "OnlineParams") -> "OnlineParams":
    """Build a TinEye reverse-image request or a Bing people search."""
    if "headers" not in params:
        params["headers"] = {}
    if is_image_query(query):
        image_url = extract_image_url(query)
        if image_url:
            return _tineye_request(image_url, params)
    return _bing_request(query, params)


def _parse_tineye(resp: "SXNG_Response") -> list[dict[str, t.Any]]:
    results: list[dict[str, t.Any]] = []

    if resp.status_code in (400, 422):
        logger.info("TinEye returned HTTP %s", resp.status_code)
        return results

    resp.raise_for_status()
    payload = resp.json()
    seen: set[str] = set()

    for match in payload.get("matches") or []:
        backlinks = match.get("backlinks") or []
        if not backlinks:
            continue
        backlink = backlinks[0]
        page_url = backlink.get("backlink") or ""
        network = match_social_network(page_url)
        if not network or page_url in seen:
            continue
        seen.add(page_url)

        crawl_date = backlink.get("crawl_date")
        published = None
        if crawl_date:
            try:
                published = datetime.fromisoformat(crawl_date)
            except ValueError:
                published = None

        image_url = match.get("image_url") or backlink.get("url")
        score = match.get("score")
        score_txt = f" · match {score:.0f}%" if isinstance(score, (int, float)) else ""
        handle = backlink.get("image_name") or _host(page_url)

        results.append(
            {
                "template": "images.html",
                "url": page_url,
                "title": f"{network}: {handle}",
                "content": f"{network}{score_txt}",
                "thumbnail_src": image_url,
                "img_src": backlink.get("url") or image_url,
                "source": network,
                "format": match.get("format"),
                "width": match.get("width"),
                "height": match.get("height"),
                "publishedDate": published,
            }
        )

    return results


def _parse_bing(resp: "SXNG_Response") -> list[dict[str, t.Any]]:
    results: list[dict[str, t.Any]] = []
    dom = html.fromstring(resp.text)
    seen: set[str] = set()

    for item in eval_xpath_list(dom, '//ol[@id="b_results"]/li[contains(@class, "b_algo")]'):
        link = eval_xpath_getindex(item, ".//h2/a", 0, None)
        if link is None:
            continue
        href = decode_bing_href(link.attrib.get("href", ""))
        title = extract_text(link)
        if not href or not title:
            continue
        network = match_social_network(href)
        if not network or href in seen:
            continue
        seen.add(href)

        content_els = eval_xpath(item, ".//p")
        for paragraph in content_els:
            for icon in paragraph.xpath('.//span[@class="algoSlug_icon"]'):
                parent = icon.getparent()
                if parent is not None:
                    parent.remove(icon)
        snippet = extract_text(content_els) or ""

        results.append(
            {
                "url": href,
                "title": f"{network}: {title}",
                "content": snippet,
                "source": network,
            }
        )

    return results


def response(resp: "SXNG_Response") -> list[dict[str, t.Any]]:
    """Parse TinEye JSON or Bing HTML and keep social-network hits only."""
    url = str(getattr(resp, "url", "") or "")
    if "tineye.com" in url:
        return _parse_tineye(resp)
    return _parse_bing(resp)
