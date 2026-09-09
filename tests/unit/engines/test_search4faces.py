# SPDX-License-Identifier: AGPL-3.0-or-later
# pylint: disable=missing-module-docstring,missing-class-docstring

from unittest.mock import Mock, patch

from searx.engines import search4faces
from tests import SearxTestCase

DETECT_OK = {
    "jsonrpc": "2.0",
    "id": "1",
    "result": {
        "image": "5eb16c3421dd32.08349177.jpg",
        "faces": [
            {
                "x": 25,
                "y": 37,
                "width": 55,
                "height": 67,
                "lm1_x": 39,
                "lm1_y": 68,
                "lm2_x": 62,
                "lm2_y": 62,
                "lm3_x": 53,
                "lm3_y": 80,
                "lm4_x": 47,
                "lm4_y": 90,
                "lm5_x": 67,
                "lm5_y": 84,
            }
        ],
    },
}

SEARCH_OK = {
    "jsonrpc": "2.0",
    "id": "2",
    "result": {
        "profiles": [
            {
                "score": "94.70",
                "face": "https://search4faces.com/faces/vk01/00.jpg",
                "profile": "https://vk.com/id1",
                "photo": "https://vk.com/id1?z=photo1_1",
                "source": "https://sun9-3.userapi.com/c7003/face.jpg",
                "age": 35,
                "first_name": "Павел",
                "last_name": "Дуров",
                "city": "Санкт-Петербург",
                "country": "Россия",
            },
            {
                "score": "81",
                "profile": "https://vk.com/id1",
                "first_name": "dup",
            },
            {
                "score": "70.1",
                "face": "https://search4faces.com/faces/tt/1.jpg",
                "profile": "https://www.tiktok.com/@someone",
                "first_name": "Someone",
                "last_name": "",
            },
        ]
    },
}


class TestSearch4FacesEngine(SearxTestCase):

    def test_categories(self):
        self.assertIn("social media", search4faces.categories)
        self.assertIn("images", search4faces.categories)

    def test_setup_without_key(self):
        self.assertFalse(search4faces.setup({"api_key": ""}))
        self.assertFalse(search4faces.setup({"api_key": "..."}))
        self.assertFalse(search4faces.setup({}))

    def test_setup_with_key(self):
        self.assertTrue(search4faces.setup({"api_key": "5c40b9-b246ab-648561"}))

    def test_split_query(self):
        self.assertEqual(
            search4faces.split_query("vk_wall https://x.jpg"),
            ("vk_wall", "https://x.jpg"),
        )
        self.assertEqual(
            search4faces.split_query("tiktok data:image/jpeg;base64,abc"),
            ("tiktok", "data:image/jpeg;base64,abc"),
        )
        self.assertEqual(
            search4faces.split_query("https://x.jpg"),
            (None, "https://x.jpg"),
        )
        self.assertEqual(
            search4faces.split_query("unknown https://x.jpg"),
            (None, "unknown https://x.jpg"),
        )

    def test_resolve_source(self):
        self.assertEqual(search4faces.resolve_source("tiktok"), "tt_avatar")
        self.assertEqual(search4faces.resolve_source("vk"), "vk_wall")
        self.assertEqual(search4faces.resolve_source(None), search4faces.s4f_source)

    def test_extract_image_payload_data_uri(self):
        payload = search4faces.extract_image_payload("data:image/jpeg;base64,abc123")
        self.assertEqual(payload, "abc123")

    def test_extract_image_payload_name_is_none(self):
        self.assertIsNone(search4faces.extract_image_payload("Ivan Petrov"))

    def test_request_skips_without_image(self):
        params = search4faces.request("Ivan Petrov", {"headers": {}, "pageno": 1})
        self.assertIsNone(params["url"])

    @patch("searx.engines.search4faces.post")
    @patch("searx.engines.search4faces.get")
    def test_request_builds_searchface(self, mock_get, mock_post):
        search4faces.api_key = "test-key"
        mock_get.return_value = Mock(ok=True, content=b"\xff\xd8" + b"\x00" * 64)
        mock_post.return_value = Mock(ok=True, json=lambda: DETECT_OK)

        params = search4faces.request(
            "vk https://cdn.example.com/face.jpg",
            {"headers": {}, "pageno": 1, "searxng_locale": "ru"},
        )

        self.assertEqual(params["url"], search4faces.api_url)
        self.assertEqual(params["method"], "POST")
        self.assertEqual(params["headers"]["x-authorization-token"], "test-key")
        body = params["json"]
        self.assertEqual(body["method"], "searchFace")
        self.assertEqual(body["params"]["image"], "5eb16c3421dd32.08349177.jpg")
        self.assertEqual(body["params"]["source"], "vk_wall")
        self.assertEqual(body["params"]["face"]["x"], 25)
        mock_get.assert_called()
        mock_post.assert_called()
        search4faces.api_key = ""

    @patch("searx.engines.search4faces.post")
    def test_request_data_uri_skips_download(self, mock_post):
        search4faces.api_key = "test-key"
        mock_post.return_value = Mock(ok=True, json=lambda: DETECT_OK)
        params = search4faces.request(
            "data:image/jpeg;base64,abc123",
            {"headers": {}, "pageno": 1, "language": "en-US"},
        )
        self.assertEqual(params["json"]["params"]["lang"], "en")
        self.assertEqual(params["json"]["method"], "searchFace")
        search4faces.api_key = ""

    @patch("searx.engines.search4faces.post")
    def test_request_no_faces_skips(self, mock_post):
        mock_post.return_value = Mock(ok=True, json=lambda: {"result": {"image": "x", "faces": []}})
        params = search4faces.request(
            "data:image/jpeg;base64,abc123",
            {"headers": {}, "pageno": 1},
        )
        self.assertIsNone(params["url"])

    def test_response_profiles(self):
        resp = Mock()
        resp.status_code = 200
        resp.json.return_value = SEARCH_OK
        results = search4faces.response(resp)
        self.assertEqual(len(results), 2)
        first = results[0]
        self.assertEqual(first["url"], "https://vk.com/id1")
        self.assertIn("Павел", first["title"])
        self.assertIn("95% match", first["content"])
        self.assertIn("Санкт-Петербург", first["content"])
        self.assertEqual(first["template"], "images.html")
        self.assertEqual(first["thumbnail_src"], "https://sun9-3.userapi.com/c7003/face.jpg")
        self.assertEqual(results[1]["url"], "https://www.tiktok.com/@someone")

    def test_response_rpc_error(self):
        resp = Mock()
        resp.status_code = 200
        resp.json.return_value = {"error": {"code": -32000, "message": "no quota"}}
        self.assertEqual(len(search4faces.response(resp)), 0)

    def test_response_http_error(self):
        resp = Mock()
        resp.status_code = 403
        resp.json.return_value = {}
        self.assertEqual(len(search4faces.response(resp)), 0)
