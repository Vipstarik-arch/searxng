# SPDX-License-Identifier: AGPL-3.0-or-later
# pylint: disable=missing-module-docstring,missing-class-docstring

from datetime import datetime
from urllib.parse import parse_qs, unquote, urlparse

import mock
from requests import HTTPError

from searx.engines import social_face
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
            "https://example.com/photo": None,
            "https://notvk.com/id1": None,
            "": None,
        }
        for url, expected in cases.items():
            self.assertEqual(social_face.match_social_network(url), expected, url)

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

    def test_decode_bing_href_passthrough(self):
        url = "https://vk.com/id1"
        self.assertEqual(social_face.decode_bing_href(url), url)

    def test_decode_bing_href_unwraps_ck(self):
        raw = "https://instagram.com/user"
        encoded = base64_url(raw)
        href = f"https://www.bing.com/ck/a?u=a1{encoded}"
        self.assertEqual(social_face.decode_bing_href(href), raw)

    def test_request_image_uses_tineye(self):
        params = social_face.request(
            "https://cdn.example.com/face.jpg",
            {"pageno": 2, "headers": {}},
        )
        parsed = urlparse(params["url"])
        self.assertEqual(parsed.netloc, "tineye.com")
        self.assertIn("/api/v1/result_json/", parsed.path)
        query = parse_qs(parsed.query)
        self.assertEqual(query["page"][0], "2")
        self.assertEqual(unquote(query["url"][0]), "https://cdn.example.com/face.jpg")
        self.assertFalse(params["raise_for_httperror"])

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

    def test_response_tineye_client_error_empty(self):
        resp = mock.Mock()
        resp.url = "https://tineye.com/api/v1/result_json/"
        resp.status_code = 422
        resp.json.return_value = {"suggestions": {"key": "Download Error"}}
        results = social_face.response(resp)
        self.assertEqual(results, [])

    def test_response_tineye_http_error_raises(self):
        resp = mock.Mock()
        resp.url = "https://tineye.com/api/v1/result_json/"
        resp.status_code = 500
        resp.raise_for_status.side_effect = HTTPError()
        self.assertRaises(HTTPError, social_face.response, resp)

    def test_response_bing_filters_to_socials(self):
        resp = mock.Mock()
        resp.url = "https://www.bing.com/search?q=Ivan"
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
        resp.text = "<html><body><ol id='b_results'></ol></body></html>"
        self.assertEqual(social_face.response(resp), [])


def base64_url(text: str) -> str:
    """URL-safe base64 without padding, as used in Bing ``u=a1…`` links."""
    import base64

    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")
