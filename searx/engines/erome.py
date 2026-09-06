# SPDX-License-Identifier: AGPL-3.0-or-later
"""EroMe_ is an adult (18+) hosting site for image & video albums.  There is no
official API, this engine scrapes the public search page
``https://www.erome.com/search?q=<query>``.

This engine is part of the ``adult`` category (tab), it does not show up in
the ``videos`` category of a default instance.

Because EroMe albums mix photos *and* videos, and the site itself does not
offer a "videos only" filter, this engine filters the search results for
albums that actually contain videos (see :py:obj:`only_videos`).  A search
for a tag like ``!ero big`` is therefore a real *video* search.

Configuration
=============

.. code:: yaml

  - name: erome
    engine: erome
    shortcut: ero
    timeout: 6.0
    # order: new          # "hot" (default) sorts by popularity, "new" by upload date
    # only_videos: false  # also include photo-only albums in the result list

Implementations
===============

The result list is parsed in three stages, the first stage that yields
results wins:

1. *structured*: parse the album cards of ``div#albums`` using the classes
   ``album-title``, ``album-thumbnail``, ``album-user``, ``album-videos`` and
   ``album-bottom-views``.
2. *heuristic*: album cards are anchored by a link that contains a preview
   image, the album title is a second link with the same ``href`` but without
   an image.
3. *regex*: as a last resort album IDs are scraped from the raw HTML (this
   is what gallery-dl does).

When EroMe rate limits an instance it serves a *"Please wait a few
moments"* interstitial page.  In this case the engine raises
:class:`~searx.exceptions.SearxEngineTooManyRequestsException`, which
suspends the engine for an hour instead of hammering the site.

.. _EroMe: https://www.erome.com

"""

import re
from urllib.parse import urlencode, urljoin, urlparse

from lxml import html

from searx.exceptions import SearxEngineTooManyRequestsException
from searx.utils import extract_text

about = {
    "website": "https://www.erome.com",
    "wikidata_id": None,
    "official_api_documentation": None,
    "use_official_api": False,
    "require_api_key": False,
    "results": "HTML",
}

# engine dependent config
categories = ["adult"]
paging = True
safesearch = False
"""The site has no safesearch, it is 18+ only."""

base_url = "https://www.erome.com"
search_url = base_url + "/search"

order = "hot"
"""Result order: ``hot`` (most viewed) or ``new``."""

only_videos = True
"""EroMe albums may contain photos, videos or both.  With ``only_videos``
enabled (default) albums without a single video are dropped from the result
list, so ``!ero <tag>`` is a video search.  Set ``only_videos: false`` to
also list photo albums."""

ALBUM_ID_RE = re.compile(r"[\w-]+")
"""An album ID is the last part of ``https://www.erome.com/a/<album_id>``."""

CARDS_XPATH = '//div[@id="albums"]/*[self::div]'
"""Album cards of the structured layout (one ``div`` per album)."""

ALBUM_ANCHOR_XPATH = (
    '//a[img and (contains(@href, "://www.erome.com/a/") or starts-with(@href, "/a/"))]'
)
"""Heuristic layout: an album card is anchored by a link that contains a
preview image."""

INTERSTITIAL_MARKER = "Please wait a few moments"
"""EroMe serves this page instead of the results when an instance is rate
limited."""

ALBUM_ABS_URL_RE = re.compile(r"https?://(?:www\.)?erome\.com/a/([\w-]+)")
ALBUM_REL_URL_RE = re.compile(r'href="/a/([\w-]+)"')
THUMBNAIL_RE = re.compile(
    r'https://s\d+\.erome\.com/[^"\']*?/[^\s"\']*?/thumbs/[^\s"\']+'
)

NUMBER_RE = re.compile(r"^\s*([\d.,\s]*\d)\s*([KkMm])?\s*$")
"""View / count badges: ``852K``, ``75,5K``, ``12,5M``, ``3`` -- the ``18+``
badge does not match because of its trailing ``+``."""


def request(query, params):
    args = {"q": query}
    if order == "new":
        args["o"] = "new"
    if params["pageno"] > 1:
        args["page"] = params["pageno"]
    params["url"] = f"{search_url}?{urlencode(args)}"
    return params


def response(resp):
    body = resp.text or ""
    if INTERSTITIAL_MARKER in body[:2000]:
        # rate limited: suspend this engine for a while (SearXNG default 1h)
        raise SearxEngineTooManyRequestsException(
            message="erome: anti-bot interstitial"
        )

    doc = html.fromstring(body)

    results, cards_seen, video_badges_seen = _parse_cards(doc)

    if not cards_seen:
        # site redesign?  try the heuristic and at the very end raw regex
        results = _parse_anchors(doc)
        if not results:
            results = _parse_regex(body)

    elif only_videos and video_badges_seen == 0:
        # none of the cards carries an ``album-videos`` badge: the selector
        # is most likely outdated, do not filter everything away
        pass

    elif only_videos:
        results = [r for r in results if r.get("_video_count")]

    for result in results:
        result.pop("_video_count", None)

    return results


def _parse_cards(doc):
    """Structured parser: album cards in ``div#albums``."""

    results = []
    cards_seen = 0
    video_badges_seen = 0
    seen = set()

    for card in doc.xpath(CARDS_XPATH):
        url, album_id = _card_url(card)
        if not url or album_id in seen:
            continue

        thumbnail = _card_thumbnail(card)
        if not thumbnail:
            # without a preview image the card is useless in a media search
            continue

        cards_seen += 1
        seen.add(album_id)

        views = _parse_number(_el_text(_by_class(card, "album-bottom-views")))
        videos_el = _by_class(card, "album-videos")
        video_count = 0
        if videos_el is not None:
            video_badges_seen += 1
            video_count = _parse_number(_el_text(videos_el))
            if video_count is None:
                # badge is present but unreadable --> an album tagged as
                # "video album" has at least one video
                video_count = 1

        results.append(
            {
                "template": "videos.html",
                "url": url,
                "title": _card_title(card, url) or album_id,
                "thumbnail": thumbnail,
                "author": _el_text(_by_class(card, "album-user")),
                "views": views,
                "content": _content(video_count, views),
                "_video_count": video_count,
            }
        )

    return results, cards_seen, video_badges_seen


def _card_url(card):
    """URL & album ID of a card, the ``album-title`` link is preferred."""

    title_el = _by_class(card, "album-title")
    if title_el is not None and title_el.tag == "a" and title_el.get("href"):
        href = title_el.get("href")
    else:
        anchors = card.xpath('.//a[contains(@href, "/a/")]')
        if not anchors:
            return None, None
        href = anchors[0].get("href") or ""

    url = urljoin(base_url + "/", href)
    album_id = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
    if not ALBUM_ID_RE.fullmatch(album_id):
        return None, None
    return url, album_id


def _card_thumbnail(card):
    """Preview image of a card, ``data-src`` (lazyload) wins over ``src``."""

    imgs = _by_class_all(card, "album-thumbnail", tag="img")
    imgs += [img for img in card.xpath(".//img") if img not in imgs]
    for img in imgs:
        src = img.get("data-src") or img.get("src") or ""
        if src.startswith("data:"):
            continue  # 1px GIF placeholder
        if "avatar." in src:
            continue  # uploader avatar, not the album preview
        if src.startswith("//"):
            src = "https:" + src
        if src.startswith("http"):
            return src
    return None


def _card_title(card, url):
    """Title of a card; falls back to a linkless image caption."""

    title_el = _by_class(card, "album-title")
    if title_el is not None:
        title = extract_text(title_el).strip()
        if title:
            return title

    # the title is also a link with the same href but without an image inside
    for anchor in card.xpath(".//a[not(img)]"):
        anchor_url = urljoin(base_url + "/", anchor.get("href") or "")
        if anchor_url == url:
            title = extract_text(anchor).strip()
            if title:
                return title

    for name in ("alt", "title"):
        for img in card.xpath(".//img"):
            value = (img.get(name) or "").strip()
            if value:
                return value
    return None


def _parse_anchors(doc):
    """Heuristic parser for pages without the ``#albums`` layout."""

    results = []
    seen = set()

    for anchor in doc.xpath(ALBUM_ANCHOR_XPATH):
        href = anchor.get("href") or ""
        url = urljoin(base_url + "/", href)
        album_id = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]

        if not ALBUM_ID_RE.fullmatch(album_id or "") or album_id in seen:
            continue

        thumbnail = _thumbnail(anchor)
        if not thumbnail:
            continue
        seen.add(album_id)

        views = _views_from_anchor(anchor)
        results.append(
            {
                "template": "videos.html",
                "url": url,
                "title": _anchor_title(doc, url, href, album_id),
                "thumbnail": thumbnail,
                "views": views,
                "content": _content(None, views),
            }
        )

    return results


def _thumbnail(anchor):
    for img in anchor.xpath("./img"):
        src = img.get("data-src") or img.get("src") or ""
        if src.startswith("data:"):
            continue
        if src.startswith("//"):
            src = "https:" + src
        if src.startswith("http"):
            return src
    return None


def _anchor_title(doc, url, href, album_id):
    # the title is a link with the same href but without an image inside
    if '"' not in href:
        path = urlparse(url).path
        title_el = doc.xpath(f'//a[@href="{href}" or @href="{path}"][not(img)][1]')
        if title_el:
            title = extract_text(title_el[0]).strip()
            if title:
                return title
    return album_id


def _views_from_anchor(anchor):
    """Best effort view count from the text nodes of a preview anchor.
    Suffixes (``K`` / ``M``) are the most reliable candidates, plain numbers
    might also be a media count badge."""

    candidates = []
    for text in anchor.xpath("./text()"):
        number = _parse_number(text)
        if number is not None:
            candidates.append((bool(NUMBER_RE.match(text.strip()).group(2)), number))

    for _, number in sorted(candidates, key=lambda c: not c[0]):
        return number
    return None


def _parse_regex(body):
    """Last resort: scrape album IDs from the raw HTML (gallery-dl style)."""

    results = []
    seen = set()
    thumbs = THUMBNAIL_RE.findall(body)

    for album_id in ALBUM_ABS_URL_RE.findall(body) + ALBUM_REL_URL_RE.findall(body):
        if album_id in seen:
            continue
        seen.add(album_id)
        results.append(
            {
                "template": "videos.html",
                "url": f"{base_url}/a/{album_id}",
                "title": album_id,
                "thumbnail": next((t for t in thumbs if f"/{album_id}/" in t), None),
            }
        )

    return results


def _content(video_count, views):
    parts = []
    if video_count:
        parts.append(f"{video_count} video" + ("s" if video_count != 1 else ""))
    if views:
        parts.append(f"{views:,} views")
    return " · ".join(parts)


def _parse_number(text):
    """Parse badges like ``852K`` / ``75,5K`` / ``12,5M`` / ``3`` into an
    int.  Returns None for ``18+`` badges, texts without digits and values
    that can't be converted."""

    if not text:
        return None
    match = NUMBER_RE.match(text.strip())
    if not match:
        return None

    number = match.group(1).replace(" ", "")
    suffix = (match.group(2) or "").lower()

    try:
        if suffix:
            # K/M badges use a decimal comma and no thousands separator
            return int(
                float(number.replace(".", "").replace(",", "."))
                * {"k": 1000, "m": 1_000_000}[suffix]
            )
        return int(float(number.replace(",", ".")))
    except ValueError:
        return None


def _by_class(element, name, tag=None):
    """First descendant of ``element`` whose class list contains ``name``."""
    for sub in _by_class_all(element, name, tag=tag):
        return sub
    return None


def _by_class_all(element, name, tag=None):
    """Descendants of ``element`` whose class list contains ``name`` (exact
    token match, ``album-title`` does not match ``album-title-page``)."""
    xpath = f".//{tag}" if tag else ".//*"
    return [sub for sub in element.xpath(xpath) if name in (sub.classes or [])]


def _el_text(element):
    """``extract_text`` that also accepts None and strips the result."""
    if element is None:
        return None
    return extract_text(element).strip() or None
