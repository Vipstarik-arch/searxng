# SPDX-License-Identifier: AGPL-3.0-or-later
# pylint: disable=missing-module-docstring,missing-class-docstring

from datetime import datetime
from urllib.parse import parse_qs, unquote, urlparse

import mock
from requests import HTTPError

from searx.engines import social_face
from searx.exceptions import SearxEngineCaptchaException
from tests import SearxTestCase

BING_HTML = """
<html><body>
<ol id="b_results">
  <li class="b_algo">
    <h2><a href="https://vk.com/id12345">Ivan Petrov</a></h2>
    <p>Public profile on VK</p>
  </li>
  <li class="b_algo">
    <h2><a href="https://www.instagram.com/ivan.p/">ivan.p</a></h2>
    <p>Instagram photos</p>
  </li>
  <li class="b_algo">
    <h2><a href="https://example.com/not-a-social-network">Skip me</a></h2>
    <p>Unrelated website</p>
  </li>
  <li class="b_algo">
    <h2><a href="https://ok.ru/profile/777">Ivan on OK</a></h2>
    <p>Odnoklassniki</p>
  </li>
  <li class="b_algo">
    <h2><a href="https://vk.com/id12345">duplicate VK</a></h2>
    <p>same profile twice</p>
  </li>
</ol>
</body></html>
"""

YANDEX_HTML = """
<html><body>
<div class="CbirSites-Item">
  <a class="CbirSites-ItemTitle" href="https://vk.com/id1">Pavel Durov</a>
  <img src="https://sun.userapi.com/face.jpg">
</div>
<div class="CbirSites-Item">
  <a href="https://ok.ru/profile/2">OK profile</a>
</div>
<div class="CbirSites-Item">
  <a href="https://example.com/stock">stock photo</a>
</div>
<script>{"url":"https://www.tiktok.com/@someone"}</script>
</body></html>
"""

TINEYE_MATCHES = {
    "matches": [
        {
            "image_url": "https://tineye.example/thumb1.jpg",
            "score": 92.4,
            "format": "JPEG",
            "width": 640,
            "height": 480,
            "backlinks": [
                {
                    "url": "https://cdn.vk.com/photo.jpg",
                    "backlink": "https://vk.com/id999",
                    "crawl_date": "2020-05-25",
                    "image_name": "id999.jpg",
                }
            ],
        },
        {
            "image_url": "https://tineye.example/thumb2.jpg",
            "score": 40,
            "backlinks": [
                {
                    "url": "https://stock.example/photo.jpg",
                    "backlink": "https://shutterstock.com/photo/1",
                    "image_name": "stock.jpg",
                }
            ],
        },
        {
            "image_url": "https://tineye.example/thumb3.jpg",
            "backlinks": [
                {
                    "url": "https://scontent.cdninstagram.com/p.jpg",
                    "backlink": "https://www.instagram.com/p/AbCdEf/",
                    "image_name": "AbCdEf.jpg",
                }
            ],
        },
        {
            "image_url": "https://tineye.example/empty.jpg",
            "backlinks": [],
        },
    ]
}


class TestSocialFaceEngine(SearxTestCase):

    def test_categories(self):
        self.assertIn("social media", social_face.categories)
        self.assertIn("images", social_face.categories)

    def test_match_social_network(self):
        cases = {
            "https://vk.com/id1": "VK",
            "https://m.vk.com/id1": "VK",
            "https://www.instagram.com/user/": "Instagram",
            "https://facebook.com/user": "Facebook",
            "https://www.facebook.com/user": "Facebook",
            "https://ok.ru/profile/1": "Odnoklassniki",
            "https://www.tiktok.com/@user": "TikTok",
            "https://x.com/user": "X",
            "https://twitter.com/user": "X",
            "https://www.linkedin.com/in/user": "LinkedIn",
            "https://t.me/username": "Telegram",
            "https://youtube.com/@user": "YouTube",
            "https://youtu.be/abc": "YouTube",
            "https://www.threads.net/@user": "Threads",
            "https://www.clubhouse.com/@user": "Clubhouse",
            "https://example.com/photo": None,
            "https://notvk.com/id1": None,
            "": None,
        }
        for url, expected in cases.items():
            self.assertEqual(social_face.match_social_network(url), expected, url)

    def test_split_query(self):
        self.assertEqual(social_face.split_query("vk https://cdn.example.com/a.jpg"), ("vk", "https://cdn.example.com/a.jpg"))
        self.assertEqual(social_face.split_query("source:tiktok https://x.jpg"), ("tiktok", "https://x.jpg"))
        self.assertEqual(social_face.split_query("https://cdn.example.com/a.jpg"), (None, "https://cdn.example.com/a.jpg"))
        self.assertEqual(social_face.split_query("Ivan Petrov"), (None, "Ivan Petrov"))

    def test_is_image_query(self):
        self.assertTrue(social_face.is_image_query("https://cdn.example.com/a.jpg"))
        self.assertTrue(social_face.is_image_query("https://cdn.example.com/a.PNG?w=1"))
        self.assertTrue(social_face.is_image_query("https://cdn.example.com/photo"))
        self.assertTrue(social_face.is_image_query("data:image/jpeg;base64,abc123"))
        self.assertFalse(social_face.is_image_query("Ivan Petrov"))
        self.assertFalse(social_face.is_image_query(""))
        self.assertFalse(social_face.is_image_query("look at https://example.com/a.jpg later"))

    def test_extract_image_url(self):
        self.assertEqual(
            social_face.extract_image_url("https://cdn.example.com/a.jpg"),
            "https://cdn.example.com/a.jpg",
        )
        self.assertTrue(
            social_face.extract_image_url("data:image/png;base64,abc").startswith("data:image/")
        )
        self.assertIsNone(social_face.extract_image_url("Ivan Petrov"))

    def test_site_restricted_query_contains_popular_networks(self):
        q = social_face.site_restricted_query("Ivan Petrov")
        self.assertTrue(q.startswith("Ivan Petrov ("))
        for domain in ("vk.com", "instagram.com", "facebook.com", "ok.ru", "tiktok.com", "t.me"):
            self.assertIn(f"site:{domain}", q)
        self.assertNotIn("wikipedia.org", q)

    def test_site_restricted_query_vk_only(self):
        q = social_face.site_restricted_query("Ivan", "vk")
        self.assertIn("site:vk.com", q)
        self.assertNotIn("instagram.com", q)

    def test_decode_bing_href_passthrough(self):
        url = "https://vk.com/id1"
        self.assertEqual(social_face.decode_bing_href(url), url)

    def test_decode_bing_href_unwraps_ck(self):
        raw = "https://instagram.com/user"
        encoded = base64_url(raw)
        href = f"https://www.bing.com/ck/a?u=a1{encoded}"
        self.assertEqual(social_face.decode_bing_href(href), raw)

    def test_request_image_uses_yandex(self):
        params = social_face.request(
            "https://cdn.example.com/face.jpg",
            {"pageno": 1, "headers": {}},
        )
        parsed = urlparse(params["url"])
        self.assertIn("yandex.", parsed.netloc)
        query = parse_qs(parsed.query)
        self.assertEqual(query["rpt"][0], "imageview")
        self.assertEqual(query["cbir_page"][0], "sites")
        self.assertEqual(unquote(query["url"][0]), "https://cdn.example.com/face.jpg")
        self.assertFalse(params["raise_for_httperror"])

    def test_request_image_with_source_prefix(self):
        params = social_face.request("vk https://cdn.example.com/face.jpg", {"pageno": 1, "headers": {}})
        query = parse_qs(urlparse(params["url"]).query)
        self.assertEqual(unquote(query["url"][0]), "https://cdn.example.com/face.jpg")

    def test_request_data_image_uses_tineye(self):
        params = social_face.request("data:image/jpeg;base64,abc", {"pageno": 1, "headers": {}})
        self.assertIn("tineye.com", params["url"])
        self.assertIn("data%3Aimage", params["url"])

    def test_request_name_uses_bing(self):
        params = social_face.request("Ivan Petrov", {"pageno": 3, "headers": {}})
        parsed = urlparse(params["url"])
        self.assertEqual(parsed.netloc, "www.bing.com")
        query = parse_qs(parsed.query)
        self.assertIn("Ivan Petrov", query["q"][0])
        self.assertIn("site:vk.com", query["q"][0])
        self.assertEqual(query["first"][0], "21")

    def test_response_tineye_filters_to_socials(self):
        resp = mock.Mock()
        resp.url = "https://tineye.com/api/v1/result_json/?page=1&url=x"
        resp.status_code = 200
        resp.search_params = {"query": "https://x.jpg"}
        resp.json.return_value = TINEYE_MATCHES
        results = social_face.response(resp)

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["url"], "https://vk.com/id999")
        self.assertEqual(results[0]["source"], "VK")
        self.assertEqual(results[0]["template"], "images.html")
        self.assertEqual(results[0]["publishedDate"], datetime.fromisoformat("2020-05-25"))
        self.assertIn("92%", results[0]["content"])
        self.assertEqual(results[1]["source"], "Instagram")
        self.assertEqual(results[1]["url"], "https://www.instagram.com/p/AbCdEf/")

    def test_response_tineye_source_filter(self):
        resp = mock.Mock()
        resp.url = "https://tineye.com/api/v1/result_json/"
        resp.status_code = 200
        resp.search_params = {"query": "vk https://x.jpg"}
        resp.json.return_value = TINEYE_MATCHES
        results = social_face.response(resp)
        self.assertEqual([r["source"] for r in results], ["VK"])

    def test_response_tineye_client_error_empty(self):
        resp = mock.Mock()
        resp.url = "https://tineye.com/api/v1/result_json/"
        resp.status_code = 422
        resp.search_params = {}
        resp.json.return_value = {"suggestions": {"key": "Download Error"}}
        results = social_face.response(resp)
        self.assertEqual(results, [])

    def test_response_tineye_http_error_raises(self):
        resp = mock.Mock()
        resp.url = "https://tineye.com/api/v1/result_json/"
        resp.status_code = 500
        resp.search_params = {}
        resp.raise_for_status.side_effect = HTTPError()
        self.assertRaises(HTTPError, social_face.response, resp)

    def test_response_yandex_filters_to_socials(self):
        resp = mock.Mock()
        resp.url = "https://yandex.com/images/search?rpt=imageview"
        resp.status_code = 200
        resp.headers = {}
        resp.search_params = {"query": "https://cdn.example.com/face.jpg"}
        resp.text = YANDEX_HTML
        results = social_face.response(resp)
        urls = [item["url"] for item in results]
        self.assertEqual(urls[0], "https://vk.com/id1")
        self.assertEqual(urls[1], "https://ok.ru/profile/2")
        self.assertNotIn("https://example.com/stock", urls)
        self.assertEqual(results[0]["source"], "VK")
        self.assertEqual(results[0]["template"], "images.html")

    def test_response_yandex_regex_fallback(self):
        resp = mock.Mock()
        resp.url = "https://yandex.com/images/search?rpt=imageview"
        resp.status_code = 200
        resp.headers = {}
        resp.search_params = {"query": "https://cdn.example.com/face.jpg"}
        resp.text = '<html><body>{"href":"https://vk.com/durov"}</body></html>'
        results = social_face.response(resp)
        self.assertEqual(results[0]["url"], "https://vk.com/durov")

    def test_response_yandex_empty_no_fallback_without_query(self):
        resp = mock.Mock()
        resp.url = "https://yandex.com/images/search?rpt=imageview"
        resp.status_code = 200
        resp.headers = {}
        resp.search_params = {}
        resp.text = "<html><body>nothing</body></html>"
        self.assertEqual(social_face.response(resp), [])

    def test_response_yandex_captcha_without_query_raises(self):
        resp = mock.Mock()
        resp.url = "https://yandex.com/showcaptcha?x=1"
        resp.status_code = 200
        resp.headers = {}
        resp.search_params = {}
        resp.text = "captcha"
        with self.assertRaises(SearxEngineCaptchaException):
            social_face.response(resp)

    def test_response_bing_filters_to_socials(self):
        resp = mock.Mock()
        resp.url = "https://www.bing.com/search?q=Ivan"
        resp.search_params = {"query": "Ivan Petrov"}
        resp.text = BING_HTML
        results = social_face.response(resp)

        urls = [item["url"] for item in results]
        self.assertEqual(
            urls,
            [
                "https://vk.com/id12345",
                "https://www.instagram.com/ivan.p/",
                "https://ok.ru/profile/777",
            ],
        )
        self.assertTrue(results[0]["title"].startswith("VK:"))
        self.assertEqual(results[1]["source"], "Instagram")
        self.assertEqual(results[2]["source"], "Odnoklassniki")

    def test_response_bing_empty(self):
        resp = mock.Mock()
        resp.url = "https://www.bing.com/search?q=nobody"
        resp.search_params = {}
        resp.text = "<html><body><ol id='b_results'></ol></body></html>"
        self.assertEqual(social_face.response(resp), [])


def base64_url(text: str) -> str:
    """URL-safe base64 without padding, as used in Bing ``u=a1…`` links."""
    import base64

    return base64.urlsafe_b64encode(text.encode("ascii")).decode("ascii").rstrip("=")
