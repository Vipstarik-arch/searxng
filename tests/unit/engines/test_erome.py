# SPDX-License-Identifier: AGPL-3.0-or-later
# pylint: disable=missing-module-docstring

from urllib.parse import parse_qs, urlparse

import mock

from searx.engines import erome
from searx.exceptions import SearxEngineTooManyRequestsException
from tests import SearxTestCase

STRUCTURED_HTML = """
<html><body>
<div id="albums">
  <div class="album">
    <a class="album-link" href="/a/NBkd0ih0">
      <img class="album-thumbnail lazyload" src="data:image/gif;base64,R0lGODlhAQABAAAAACw="
           data-src="https://s77.erome.com/7453/NBkd0ih0/thumbs/i5OwkmJB.jpg?v=1772176993">
      <span class="album-videos">1</span>
      <span class="album-bottom-views">1841,2K</span>
      <span class="over18">18+</span>
    </a>
    <a class="album-title" href="/a/NBkd0ih0">Bonnie Blue Album Title</a>
    <div class="album-infos"><span class="album-user">33333893fhfn</span></div>
  </div>
  <div class="album">
    <a class="album-link" href="https://www.erome.com/a/4AsbLGVl">
      <img class="album-thumbnail" src="https://s44.erome.com/4758/4AsbLGVl/thumbs/uyczxdh8.jpeg?v=1770187564">
    </a>
    <a class="album-title" href="https://www.erome.com/a/4AsbLGVl">Photo Only Album</a>
    <span class="album-user">flakypickle</span>
  </div>
  <div class="album">
    <a class="album-link" href="/a/xYz123">
      <img class="album-thumbnail" data-src="https://s90.erome.com/5307/xYz123/thumbs/LoGrCxFr.jpeg?v=1">
      <span class="album-videos">3</span>
      <span class="album-bottom-views">12,5M</span>
    </a>
    <a class="album-title" href="/a/xYz123">Three Videos Album</a>
    <span class="album-user">PleasureUnlocked</span>
  </div>
  <div class="album">
    <a class="album-link" href="/a/nothumb">no preview image here</a>
    <a class="album-title" href="/a/nothumb">Skipped Album</a>
  </div>
</div>
</body></html>
"""

HEURISTIC_HTML = """
<html><body>
<div class="some-new-layout">
  <div>
    <a href="/a/NBkd0ih0">
      <img class="lazyload" data-src="https://s77.erome.com/7453/NBkd0ih0/thumbs/i5OwkmJB.jpg?v=1772176993">
      <img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=">
      1841,2K
    </a>
    <a href="/a/NBkd0ih0">Bonnie Blue Album Title</a>
  </div>
  <div>
    <a href="https://www.erome.com/a/4AsbLGVl">
      <img src="https://s44.erome.com/4758/4AsbLGVl/thumbs/uyczxdh8.jpeg?v=1770187564">
      11131K
    </a>
    <a href="https://www.erome.com/a/4AsbLGVl">Second Album</a>
  </div>
  <div>
    <a href="/a/small3"><img src="https://s1.erome.com/x/thumbs/small3.jpg">3</a>
    <a href="/a/small3">Three Views Album</a>
  </div>
  <div>
    <a href="/a/NoThumb">no preview image here</a>
    <a href="/a/NoThumb">Skipped Album</a>
  </div>
</div>
</body></html>
"""

REGEX_HTML = """
<html><body><script>
var data = [{u: "https://www.erome.com/a/Ab12Cd34", t: "https://s77.erome.com/7453/Ab12Cd34/thumbs/i5OwkmJB.jpg"}];
var broken = [{u: "https://www.erome.com/a/NoThumbId"}];
</script></body></html>
"""


def restore(obj, name, value):
    def decorator(func):
        def wrapper(self):
            original = getattr(obj, name)
            setattr(obj, name, value)
            try:
                func(self)
            finally:
                setattr(obj, name, original)

        return wrapper

    return decorator


class TestEromeEngine(SearxTestCase):  # pylint: disable=missing-class-docstring
    def test_categories(self):
        # the engine belongs to the dedicated "adult" tab, not to "videos"
        self.assertEqual(erome.categories, ["adult"])

    def test_request(self):
        params = erome.request("test query", {"pageno": 1, "searxng_locale": "en"})
        query = parse_qs(urlparse(params["url"]).query)
        self.assertEqual(query["q"][0], "test query")
        self.assertNotIn("page", query)
        self.assertNotIn("o", query)
        self.assertIn("https://www.erome.com/search", params["url"])

        params = erome.request("test query", {"pageno": 2, "searxng_locale": "en"})
        query = parse_qs(urlparse(params["url"]).query)
        self.assertEqual(query["page"][0], "2")

        original = erome.order
        erome.order = "new"
        try:
            params = erome.request("test query", {"pageno": 1, "searxng_locale": "en"})
            query = parse_qs(urlparse(params["url"]).query)
            self.assertEqual(query["o"][0], "new")
        finally:
            erome.order = original

    def test_response_structured(self):
        """Cards in div#albums are parsed via their CSS classes and photo
        only albums are filtered by default."""
        resp = mock.Mock(text=STRUCTURED_HTML)
        results = erome.response(resp)

        # the photo-only album and the album without preview are skipped
        self.assertEqual(len(results), 2)

        first = results[0]
        self.assertEqual(first["url"], "https://www.erome.com/a/NBkd0ih0")
        self.assertEqual(first["title"], "Bonnie Blue Album Title")
        # data-src (lazyload) wins over the data: URI placeholder
        self.assertEqual(
            first["thumbnail"],
            "https://s77.erome.com/7453/NBkd0ih0/thumbs/i5OwkmJB.jpg?v=1772176993",
        )
        self.assertEqual(first["author"], "33333893fhfn")
        self.assertEqual(first["views"], 1841200)
        self.assertEqual(first["content"], "1 video · 1,841,200 views")
        self.assertEqual(first["template"], "videos.html")

        second = results[1]
        self.assertEqual(second["url"], "https://www.erome.com/a/xYz123")
        self.assertEqual(second["views"], 12500000)
        self.assertEqual(second["content"], "3 videos · 12,500,000 views")

    def test_response_with_photos(self):
        """``only_videos: false`` keeps photo albums in the result list."""
        original = erome.only_videos
        erome.only_videos = False
        try:
            resp = mock.Mock(text=STRUCTURED_HTML)
            results = erome.response(resp)
        finally:
            erome.only_videos = original

        self.assertEqual(len(results), 3)
        self.assertEqual(results[1]["url"], "https://www.erome.com/a/4AsbLGVl")
        self.assertEqual(results[1]["title"], "Photo Only Album")
        self.assertEqual(results[1]["author"], "flakypickle")
        # no leaking of internal keys
        self.assertNotIn("_video_count", results[1])

    def test_response_badge_no_count(self):
        """An ``album-videos`` badge without readable count still marks a
        video album."""
        html = STRUCTURED_HTML.replace(
            '<span class="album-videos">1</span>', '<span class="album-videos"></span>'
        )
        resp = mock.Mock(text=html)
        results = erome.response(resp)

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["content"], "1 video · 1,841,200 views")

    def test_response_no_badges(self):
        """If none of the cards carries an ``album-videos`` badge the video
        filter is skipped (selector outdated), no empty result page."""
        html = STRUCTURED_HTML.replace(
            '<span class="album-videos">1</span>', ""
        ).replace('<span class="album-videos">3</span>', "")
        resp = mock.Mock(text=html)
        results = erome.response(resp)

        self.assertEqual(len(results), 3)

    def test_response_heuristic(self):
        """Without a div#albums layout, cards are detected by their anchors."""
        resp = mock.Mock(text=HEURISTIC_HTML)
        results = erome.response(resp)

        # the album without preview image is skipped, no duplicates
        self.assertEqual(len(results), 3)

        first = results[0]
        self.assertEqual(first["url"], "https://www.erome.com/a/NBkd0ih0")
        self.assertEqual(first["title"], "Bonnie Blue Album Title")
        self.assertEqual(
            first["thumbnail"],
            "https://s77.erome.com/7453/NBkd0ih0/thumbs/i5OwkmJB.jpg?v=1772176993",
        )
        self.assertEqual(first["views"], 1841200)

        second = results[1]
        self.assertEqual(second["url"], "https://www.erome.com/a/4AsbLGVl")
        self.assertEqual(second["title"], "Second Album")
        self.assertEqual(second["views"], 11131000)

        third = results[2]
        self.assertEqual(third["views"], 3)

    def test_response_regex_fallback(self):
        """Last resort: album IDs scraped from the raw HTML."""
        resp = mock.Mock(text=REGEX_HTML)
        results = erome.response(resp)

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["url"], "https://www.erome.com/a/Ab12Cd34")
        self.assertEqual(
            results[0]["thumbnail"],
            "https://s77.erome.com/7453/Ab12Cd34/thumbs/i5OwkmJB.jpg",
        )
        self.assertEqual(results[1]["url"], "https://www.erome.com/a/NoThumbId")
        self.assertIsNone(results[1]["thumbnail"])

    def test_response_no_results(self):
        resp = mock.Mock(text="<html><body><p>nothing here</p></body></html>")
        results = erome.response(resp)
        self.assertEqual(results, [])

    def test_response_interstitial(self):
        """The rate limit interstitial suspends the engine."""
        resp = mock.Mock(
            text="<html><title>Please wait a few moments</title><body>x</body></html>"
        )
        with self.assertRaises(SearxEngineTooManyRequestsException):
            erome.response(resp)

    def test_parse_number(self):
        parse = erome._parse_number  # pylint: disable=protected-access
        self.assertEqual(parse("852K"), 852000)
        self.assertEqual(parse("75,5K"), 75500)
        self.assertEqual(parse("12,5M"), 12500000)
        self.assertEqual(parse("1841,2K"), 1841200)
        self.assertEqual(parse("3"), 3)
        self.assertEqual(parse(" 1 "), 1)
        self.assertIsNone(parse("18+"))
        self.assertIsNone(parse(""))
        self.assertIsNone(parse(None))
        self.assertIsNone(parse("hot"))

    def test_dedupe(self):
        html = STRUCTURED_HTML.replace(
            'href="/a/xYz123"', 'href="/a/NBkd0ih0"'
        ).replace("https://www.erome.com/a/xYz123", "https://www.erome.com/a/NBkd0ih0")
        resp = mock.Mock(text=html)
        results = erome.response(resp)
        urls = [r["url"] for r in results]
        self.assertEqual(len(urls), len(set(urls)))
