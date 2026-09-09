# SPDX-License-Identifier: AGPL-3.0-or-later
"""Search4Faces_ official JSON-RPC API.

Search4Faces detects faces on a photo and looks them up in crawled social
network databases (VK profile photos, VK/OK avatars, TikTok, Clubhouse,
public figures).  This is **face recognition against their index**, not
generic reverse-image search.

The engine talks to the documented JSON-RPC 2.0 endpoint.  It does not scrape
the website and does not build its own face database.

A paid :py:obj:`api_key` is required (`API docs`_).  Without a key the engine
stays inactive (same pattern as Springer / CORE).

Two RPC calls run per search:

1. ``detectFaces`` — upload JPEG/PNG (base64), get face landmarks
2. ``searchFace`` — query one database (:py:obj:`s4f_source`)

Query: a public image URL or a ``data:image/…;base64,…`` URI.  Optionally
prefix a database alias::

    !s4f https://example.com/face.jpg
    !s4f vk_wall https://example.com/face.jpg
    !s4f tiktok data:image/jpeg;base64,...

.. _Search4Faces: https://search4faces.com/
.. _API docs: https://search4faces.com/api.html

Configuration
=============

.. code:: yaml

  - name: search4faces
    engine: search4faces
    shortcut: s4f
    api_key: "your-key"
    # s4f_source: vk_wall   # vk_wall, vkok_avatar, vkokn_avatar, tt_avatar, ch_avatar, sb_photo
    # s4f_hidden: false     # include hidden profiles
    # s4f_results: 10
    timeout: 15.0
    inactive: false
"""

from __future__ import annotations

import base64
import logging
import re
import typing as t
import uuid
from urllib.parse import urlparse

from searx.network import get, post
from searx.result_types import EngineResults

if t.TYPE_CHECKING:
    from searx.extended_types import SXNG_Response
    from searx.search.processors import OnlineParams

logger = logging.getLogger("searx.engines.search4faces")

about = {
    "website": "https://search4faces.com/",
    "official_api_documentation": "https://search4faces.com/api.html",
    "use_official_api": True,
    "require_api_key": True,
    "results": "JSON",
}

categories = ["social media", "images"]
paging = False
safesearch = False

api_key = ""
"""JSON-RPC key sent as ``x-authorization-token``.  Get a trial key from
https://search4faces.com/contact.html"""

api_url = "https://search4faces.com/api/json-rpc/v1"
"""JSON-RPC 2.0 endpoint."""

s4f_source = "vk_wall"
"""Default database.  ``vk_wall`` is the largest (VK profile photos)."""

s4f_hidden = False
"""If true, also return hidden profiles (Search4Faces ``hidden`` flag)."""

s4f_results = 10
"""Max profiles per search (API allows up to 500)."""

# Aliases a user may type in front of the image query.
SOURCE_ALIASES: dict[str, str] = {
    "vk": "vk_wall",
    "vk_wall": "vk_wall",
    "vkwall": "vk_wall",
    "ok": "vkok_avatar",
    "vkok": "vkok_avatar",
    "vkok_avatar": "vkok_avatar",
    "vkokn": "vkokn_avatar",
    "vkokn_avatar": "vkokn_avatar",
    "tiktok": "tt_avatar",
    "tt": "tt_avatar",
    "tt_avatar": "tt_avatar",
    "clubhouse": "ch_avatar",
    "ch": "ch_avatar",
    "ch_avatar": "ch_avatar",
    "public": "sb_photo",
    "sb": "sb_photo",
    "sb_photo": "sb_photo",
}

SOURCE_LABELS: dict[str, str] = {
    "vk_wall": "VK",
    "vkok_avatar": "VK/OK",
    "vkokn_avatar": "VK/OK 2020",
    "tt_avatar": "TikTok",
    "ch_avatar": "Clubhouse",
    "sb_photo": "Public figures",
}

_IMAGE_URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
_DATA_IMAGE_RE = re.compile(r"data:image/[^;\s]*;base64,([^\s]+)", re.IGNORECASE)
_SOURCE_PREFIX_RE = re.compile(
    r"^(?:source:)?([a-z][a-z0-9_]{1,20})\s+(.+)$",
    re.IGNORECASE,
)


def setup(engine_settings: dict[str, t.Any]) -> bool:
    """Disable the engine when no usable API key is configured."""
    key: str = engine_settings.get("api_key", "") or ""
    if key and key not in ("unset", "unknown", "...", "your-key"):
        return True
    logger.error("Search4Faces API key is not set.")
    return False


def split_query(query: str) -> tuple[str | None, str]:
    """Return ``(source_alias, rest)``.  ``https`` / ``data`` are not sources."""
    query = (query or "").strip()
    match = _SOURCE_PREFIX_RE.match(query)
    if not match:
        return None, query
    alias = match.group(1).lower()
    if alias in ("http", "https", "data"):
        return None, query
    if alias not in SOURCE_ALIASES:
        return None, query
    return alias, match.group(2).strip()


def resolve_source(alias: str | None) -> str:
    if alias and alias in SOURCE_ALIASES:
        return SOURCE_ALIASES[alias]
    return s4f_source


def extract_image_payload(query: str) -> str | None:
    """Return raw base64 of a JPEG/PNG from a data URI or http(s) URL."""
    query = (query or "").strip()
    data = _DATA_IMAGE_RE.search(query)
    if data:
        return data.group(1)

    http_url = _IMAGE_URL_RE.search(query)
    if not http_url:
        return None

    image_url = http_url.group(0)
    try:
        resp = get(image_url, allow_redirects=True)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.info("failed to download image %s: %s", image_url, exc)
        return None
    if resp is None or not getattr(resp, "ok", False):
        logger.info("image download HTTP error for %s", image_url)
        return None
    content = getattr(resp, "content", b"") or b""
    if len(content) < 32:
        return None
    return base64.b64encode(content).decode("ascii")


def _rpc_headers() -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "x-authorization-token": api_key,
        "Accept": "application/json",
    }


def _rpc_body(method: str, params: dict[str, t.Any]) -> dict[str, t.Any]:
    return {
        "jsonrpc": "2.0",
        "method": method,
        "id": str(uuid.uuid4()),
        "params": params,
    }


def detect_faces(image_b64: str) -> tuple[str, dict[str, t.Any]] | None:
    """Call ``detectFaces``.  Return ``(image_id, first_face)`` or ``None``."""
    try:
        resp = post(api_url, json=_rpc_body("detectFaces", {"image": image_b64}), headers=_rpc_headers())
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.info("detectFaces failed: %s", exc)
        return None
    if resp is None or not getattr(resp, "ok", False):
        logger.info("detectFaces HTTP %s", getattr(resp, "status_code", "?"))
        return None
    try:
        payload = resp.json()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.info("detectFaces JSON error: %s", exc)
        return None
    if payload.get("error"):
        logger.info("detectFaces RPC error: %s", payload["error"])
        return None
    result = payload.get("result") or {}
    faces = result.get("faces") or []
    image_id = result.get("image")
    if not image_id or not faces:
        return None
    return str(image_id), faces[0]


def request(query: str, params: "OnlineParams") -> "OnlineParams":
    """detectFaces, then build the searchFace POST."""
    if "headers" not in params:
        params["headers"] = {}

    alias, rest = split_query(query)
    image_b64 = extract_image_payload(rest)
    if not image_b64:
        params["url"] = None
        return params

    detected = detect_faces(image_b64)
    if not detected:
        params["url"] = None
        return params

    image_id, face = detected
    lang = str(params.get("searxng_locale") or params.get("language") or "ru")
    lang = lang.split("-")[0].split("_")[0] or "ru"

    body = _rpc_body(
        "searchFace",
        {
            "image": image_id,
            "face": face,
            "source": resolve_source(alias),
            "hidden": bool(s4f_hidden),
            "results": str(int(s4f_results)),
            "lang": lang,
        },
    )
    params["url"] = api_url
    params["method"] = "POST"
    params["json"] = body
    params["headers"].update(_rpc_headers())
    params["raise_for_httperror"] = False
    return params


def _profile_title(profile: dict[str, t.Any], network: str) -> str:
    first = str(profile.get("first_name") or "").strip()
    last = str(profile.get("last_name") or "").strip()
    name = " ".join(p for p in (first, last) if p)
    if name:
        return f"{network}: {name}"
    url = str(profile.get("profile") or "")
    handle = urlparse(url).path.strip("/") or url
    return f"{network}: {handle}"


def _profile_content(profile: dict[str, t.Any], network: str) -> str:
    parts: list[str] = [network]
    score = profile.get("score")
    if score not in (None, ""):
        try:
            parts.append(f"{float(score):.0f}% match")
        except (TypeError, ValueError):
            parts.append(f"{score}% match")
    age = profile.get("age")
    if age not in (None, "", 0):
        parts.append(f"age {age}")
    place = ", ".join(str(p) for p in (profile.get("city"), profile.get("country")) if p)
    if place:
        parts.append(place)
    return " · ".join(parts)


def response(resp: "SXNG_Response") -> EngineResults:
    """Parse ``searchFace`` profiles into image results."""
    results = EngineResults()
    if getattr(resp, "status_code", 200) >= 400:
        logger.info("searchFace HTTP %s", resp.status_code)
        return results

    try:
        payload = resp.json()
    except Exception:  # pylint: disable=broad-exception-caught
        return results

    if payload.get("error"):
        logger.info("searchFace RPC error: %s", payload["error"])
        return results

    profiles = (payload.get("result") or {}).get("profiles") or []
    seen: set[str] = set()

    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        url = str(profile.get("profile") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        source_id = str(profile.get("source_db") or s4f_source)
        network = SOURCE_LABELS.get(source_id, SOURCE_LABELS.get(s4f_source, "Social"))
        # Prefer the live social URL; fall back to the cached face crop.
        thumb = profile.get("source") or profile.get("face") or ""
        results.append(
            {
                "template": "images.html",
                "url": url,
                "title": _profile_title(profile, network),
                "content": _profile_content(profile, network),
                "thumbnail_src": thumb,
                "img_src": thumb,
                "source": network,
            }
        )

    return results
