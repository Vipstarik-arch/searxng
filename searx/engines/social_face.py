# SPDX-License-Identifier: AGPL-3.0-or-later
"""Find people on popular social networks (Search4Faces-style, no paid key).

Search4Faces is face recognition against crawled VK / OK / TikTok indexes.
This engine copies the *product* ideas that do not need their paid API:

- search **only social networks**
- several “databases” (VK, OK, TikTok, Instagram, …) selectable in the query
- photo in → profile URL + similarity out
- if one source is empty, try another backend

Backends (public indexes only, no login, no scraping of private profiles):

- **Photo URL** → Yandex CBIR “sites with this image” (best free hit-rate for
  VK/OK faces), then TinEye if Yandex returns nothing
- **data:image URI** → TinEye (Yandex cannot fetch a data URI)
- **Name** → Bing restricted with ``site:`` filters

For the official Search4Faces JSON-RPC (detectFaces + searchFace) use the
:py:mod:`searx.engines.search4faces` engine and an API key.

Shortcut: ``!sface``.

Examples::

    !sface https://example.com/face.jpg
    !sface vk https://example.com/face.jpg
    !sface tiktok https://example.com/face.jpg
    !sface Иван Петров
"""

from __future__ import annotations

import base64
import logging
import re
import typing as t
from datetime import datetime
from urllib.parse import parse_qs, urlencode, urlparse

from lxml import html

from searx.exceptions import SearxEngineCaptchaException
from searx.utils import eval_xpath, eval_xpath_getindex, eval_xpath_list, extract_text

if t.TYPE_CHECKING:
    from searx.extended_types import SXNG_Response
    from searx.search.processors import OnlineParams

logger = logging.getLogger("searx.engines.social_face")

about = {
    "website": "https://yandex.com/images/",
    "wikidata_id": "Q5281",
    "official_api_documentation": "https://search4faces.com/api.html",
    "use_official_api": False,
    "require_api_key": False,
    "results": "HTML",
}

categories = ["social media", "images"]
paging = True
safesearch = False

tineye_base_url = "https://tineye.com"
tineye_search_path = "/api/v1/result_json/?page={page}&{query}"
bing_base_url = "https://www.bing.com/search"
yandex_base_url = "https://yandex.com/images/search"

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
    "clubhouse.com": "Clubhouse",
    "imdb.com": "IMDb",
    "wikipedia.org": "Wikipedia",
}

# Search4Faces-style “databases”: restrict hits to these network names.
SOURCE_FILTERS: dict[str, frozenset[str]] = {
    "vk": frozenset({"VK"}),
    "vk_wall": frozenset({"VK"}),
    "ok": frozenset({"Odnoklassniki"}),
    "vkok": frozenset({"VK", "Odnoklassniki"}),
    "vkok_avatar": frozenset({"VK", "Odnoklassniki"}),
    "vkokn": frozenset({"VK", "Odnoklassniki"}),
    "tiktok": frozenset({"TikTok"}),
    "tt": frozenset({"TikTok"}),
    "tt_avatar": frozenset({"TikTok"}),
    "clubhouse": frozenset({"Clubhouse"}),
    "ch": frozenset({"Clubhouse"}),
    "instagram": frozenset({"Instagram"}),
    "ig": frozenset({"Instagram"}),
    "facebook": frozenset({"Facebook"}),
    "fb": frozenset({"Facebook"}),
    "telegram": frozenset({"Telegram"}),
    "linkedin": frozenset({"LinkedIn"}),
    "youtube": frozenset({"YouTube"}),
    "x": frozenset({"X"}),
    "public": frozenset({"Wikipedia", "IMDb"}),
}

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
    "clubhouse.com",
)

_IMAGE_URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
_DATA_IMAGE_RE = re.compile(r"data:image/[^;\s]*;base64,[^\s]+", re.IGNORECASE)
_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".avif")
_SOURCE_PREFIX_RE = re.compile(
    r"^(?:source:)?([a-z][a-z0-9_]{1,20})\s+(.+)$",
    re.IGNORECASE,
)
_HREF_RE = re.compile(r"""https?://[^\s"'<>\\]+""", re.IGNORECASE)


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


def split_query(query: str) -> tuple[str | None, str]:
    """Return ``(source_alias, rest)``.  URL schemes are not treated as sources."""
    query = (query or "").strip()
    match = _SOURCE_PREFIX_RE.match(query)
    if not match:
        return None, query
    alias = match.group(1).lower()
    if alias in ("http", "https", "data"):
        return None, query
    if alias not in SOURCE_FILTERS:
        return None, query
    return alias, match.group(2).strip()


_NON_SOCIAL = frozenset({"Wikipedia", "IMDb"})


def allowed_networks(source: str | None) -> frozenset[str] | None:
    """``None`` means every *social* network is allowed (not Wikipedia/IMDb)."""
    if not source:
        return None
    return SOURCE_FILTERS.get(source)


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
    return query == url


def site_restricted_query(name: str, source: str | None = None) -> str:
    """Build a Bing query restricted to popular social networks."""
    allowed = allowed_networks(source)
    domains: list[str] = []
    seen: set[str] = set()
    for domain, network in SOCIAL_NETWORKS.items():
        if domain in seen:
            continue
        if allowed is not None and network not in allowed:
            continue
        if allowed is None and network in _NON_SOCIAL:
            continue
        seen.add(domain)
        domains.append(domain)
    if not domains:
        domains = list(SITE_FILTER_DOMAINS)
    sites = " OR ".join(f"site:{domain}" for domain in domains[:15])
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


def _accepts(url: str, source: str | None) -> str | None:
    network = match_social_network(url)
    if not network:
        return None
    allowed = allowed_networks(source)
    if allowed is not None:
        return network if network in allowed else None
    if network in _NON_SOCIAL:
        return None
    return network


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


def _yandex_request(image_url: str, params: "OnlineParams") -> "OnlineParams":
    # cbir_page=sites → pages that contain the photo (profiles), not similar stock shots
    query_params = {
        "rpt": "imageview",
        "url": image_url,
        "cbir_page": "sites",
    }
    params["url"] = f"{yandex_base_url}?{urlencode(query_params)}"
    params["raise_for_httperror"] = False
    params["allow_redirects"] = True
    params["cookies"] = {"yp": "1716337604.sp.family%3A0#1685406411.szm.1:1920x1080:1920x999"}
    return params


def _bing_request(name: str, params: "OnlineParams", source: str | None) -> "OnlineParams":
    query_params: dict[str, str | int] = {
        "q": site_restricted_query(name, source),
        "first": (int(params.get("pageno", 1)) - 1) * 10 + 1,
    }
    params["url"] = f"{bing_base_url}?{urlencode(query_params)}"
    return params


def request(query: str, params: "OnlineParams") -> "OnlineParams":
    """Yandex CBIR for photo URLs, TinEye for data URIs, Bing for names."""
    if "headers" not in params:
        params["headers"] = {}
    _source, rest = split_query(query)
    if is_image_query(rest):
        image_url = extract_image_url(rest)
        if image_url:
            if image_url.startswith("data:"):
                return _tineye_request(image_url, params)
            return _yandex_request(image_url, params)
    return _bing_request(rest, params, _source)


def _query_from_resp(resp: "SXNG_Response") -> str:
    search_params = getattr(resp, "search_params", None) or {}
    if isinstance(search_params, dict):
        return str(search_params.get("query") or "")
    return ""


def _source_from_resp(resp: "SXNG_Response") -> str | None:
    source, _rest = split_query(_query_from_resp(resp))
    return source


def _parse_tineye(resp: "SXNG_Response", source: str | None) -> list[dict[str, t.Any]]:
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
        network = _accepts(page_url, source)
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
        score_txt = f" · {score:.0f}% match" if isinstance(score, (int, float)) else ""
        handle = backlink.get("image_name") or urlparse(page_url).path.strip("/") or _host(page_url)

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


def _yandex_is_captcha(resp: "SXNG_Response") -> bool:
    if resp.headers.get("x-yandex-captcha") == "captcha":
        return True
    url = str(getattr(resp, "url", "") or "")
    return "showcaptcha" in url or "/captcha" in url


def _parse_yandex_sites(dom) -> list[tuple[str, str, str]]:
    """CbirSites cards: (page_url, title, thumb)."""
    found: list[tuple[str, str, str]] = []
    for item in eval_xpath_list(
        dom, '//*[contains(@class, "CbirSites-Item") or contains(@class, "CbirSitesItem")]'
    ):
        link = eval_xpath_getindex(item, ".//a[@href][1]", 0, None)
        if link is None:
            continue
        href = (link.attrib.get("href") or "").strip()
        title = extract_text(link) or ""
        thumb = ""
        for img in item.xpath(".//img"):
            src = img.get("src") or img.get("data-src") or ""
            if src.startswith("http"):
                thumb = src
                break
        if href.startswith("http"):
            found.append((href, title, thumb))
    return found


def _parse_yandex(resp: "SXNG_Response", source: str | None) -> list[dict[str, t.Any]]:
    if _yandex_is_captcha(resp):
        raise SearxEngineCaptchaException()

    body = resp.text or ""
    results: list[dict[str, t.Any]] = []
    seen: set[str] = set()

    try:
        dom = html.fromstring(body)
        cards = _parse_yandex_sites(dom)
    except Exception:  # pylint: disable=broad-exception-caught
        cards = []

    def add_hit(page_url: str, title: str, thumb: str) -> None:
        network = _accepts(page_url, source)
        if not network or page_url in seen:
            return
        seen.add(page_url)
        handle = title.strip() or urlparse(page_url).path.strip("/") or _host(page_url)
        results.append(
            {
                "template": "images.html",
                "url": page_url,
                "title": f"{network}: {handle}",
                "content": f"{network} · Yandex similar pages",
                "thumbnail_src": thumb,
                "img_src": thumb,
                "source": network,
            }
        )

    for href, title, thumb in cards:
        add_hit(href, title, thumb)

    if results:
        return results

    # Fallback: any social URL mentioned in the page (JSON blobs, plain hrefs).
    for href in _HREF_RE.findall(body):
        href = href.rstrip("\\").rstrip(".,);")
        add_hit(href, "", "")

    return results


def _parse_bing(resp: "SXNG_Response", source: str | None) -> list[dict[str, t.Any]]:
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
        network = _accepts(href, source)
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


def _tineye_fallback(image_url: str, pageno: int, source: str | None) -> list[dict[str, t.Any]]:
    """Second backend when Yandex is empty or captcha-blocked."""
    try:
        from searx.network import get  # local import: network is thread-bound
    except Exception:  # pylint: disable=broad-exception-caught
        return []

    query = urlencode({"url": image_url})
    url = tineye_base_url + tineye_search_path.format(query=query, page=pageno)
    try:
        resp = get(url, allow_redirects=True)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.info("TinEye fallback failed: %s", exc)
        return []
    if resp is None:
        return []
    try:
        return _parse_tineye(resp, source)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.info("TinEye fallback parse failed: %s", exc)
        return []


def response(resp: "SXNG_Response") -> list[dict[str, t.Any]]:
    """Parse Yandex / TinEye / Bing and keep social-network hits only."""
    url = str(getattr(resp, "url", "") or "")
    source = _source_from_resp(resp)
    query = _query_from_resp(resp)
    _alias, rest = split_query(query)

    if "tineye.com" in url:
        return _parse_tineye(resp, source)

    if "yandex." in url or "ya.ru" in url:
        try:
            results = _parse_yandex(resp, source)
        except SearxEngineCaptchaException:
            image_url = extract_image_url(rest)
            if image_url:
                return _tineye_fallback(image_url, 1, source)
            raise
        if results:
            return results
        image_url = extract_image_url(rest)
        if image_url and not image_url.startswith("data:"):
            return _tineye_fallback(image_url, 1, source)
        return results

    return _parse_bing(resp, source)
