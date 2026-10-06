import json
import socket
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from .importing.errors import ImportFailed
from .importing.extract import extract_recipe
from .importing.fetch import ensure_public_url, fetch_page

PUBLIC_ADDRESS = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
PRIVATE_ADDRESS = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443))]

JSONLD_PAGE = """
<html><head>
<meta property="og:image" content="/img/og.jpg">
<script type="application/ld+json">{data}</script>
</head><body><h1>Інша назва</h1></body></html>
"""

TEXT_PAGE = """
<html><head>
<title>Гречаники — Смачно</title>
<meta property="og:title" content="Гречаники з м'ясом">
<meta property="og:image" content="https://example.com/grechaniki.jpg">
</head><body>
<nav>Меню сайту</nav>
<article><h1>Гречаники з м'ясом</h1>
<table><tr><td>Свинячий фарш</td><td>500 г</td></tr><tr><td>гречка</td><td>1 склянка</td></tr></table>
<p>1. Гречку відварюємо.</p>
<script>var ads = 1;</script>
</article>
<footer>© Сайт</footer>
</body></html>
"""


def response(status=200, content_type="text/html; charset=utf-8", body=b"<html></html>", location=None):
    mock = Mock()
    mock.status_code = status
    mock.headers = {"Content-Type": content_type}
    if location:
        mock.headers["Location"] = location
    mock.is_redirect = location is not None
    mock.encoding = "utf-8"
    mock.text = body.decode()
    mock.iter_content.return_value = [body]
    return mock


class ExtractTests(SimpleTestCase):
    def test_jsonld_recipe_inside_graph(self):
        data = {
            "@context": "https://schema.org",
            "@graph": [
                {"@type": "WebPage", "name": "Сторінка"},
                {
                    "@type": ["Recipe"],
                    "name": "Борщ &amp; пампушки",
                    "image": [{"@type": "ImageObject", "url": "/img/borsch.jpg"}],
                    "recipeYield": ["4 порції"],
                    "recipeIngredient": ["500 г буряка", "  2 л води "],
                    "recipeInstructions": [
                        {"@type": "HowToSection", "itemListElement": [
                            {"@type": "HowToStep", "text": "Зварити бульйон."},
                            {"@type": "HowToStep", "text": "Додати <b>буряк</b>."},
                        ]},
                    ],
                },
            ],
        }
        raw = extract_recipe(JSONLD_PAGE.format(data=json.dumps(data)), "https://example.com/borsch/")

        self.assertEqual(raw.source, "jsonld")
        self.assertEqual(raw.title, "Борщ & пампушки")
        self.assertEqual(raw.image_url, "https://example.com/img/borsch.jpg")
        self.assertEqual(raw.ingredients, ["500 г буряка", "2 л води"])
        self.assertEqual(raw.instructions, ["Зварити бульйон.", "Додати буряк."])
        self.assertEqual(raw.yield_text, "4 порції")

    def test_falls_back_to_og_image(self):
        data = {"@type": "Recipe", "name": "Салат", "recipeIngredient": ["огірок"], "recipeInstructions": "Нарізати.\nЗмішати."}
        raw = extract_recipe(JSONLD_PAGE.format(data=json.dumps(data)), "https://example.com/salad/")
        self.assertEqual(raw.image_url, "https://example.com/img/og.jpg")
        self.assertEqual(raw.instructions, ["Нарізати.", "Змішати."])

    def test_text_fallback_without_jsonld(self):
        raw = extract_recipe(TEXT_PAGE.encode("utf-8"), "https://example.com/grechaniki/")

        self.assertEqual(raw.source, "text")
        self.assertEqual(raw.title, "Гречаники з м'ясом")
        self.assertEqual(raw.image_url, "https://example.com/grechaniki.jpg")
        self.assertIn("1 склянка", raw.page_text)
        self.assertNotIn("Меню сайту", raw.page_text)
        self.assertNotIn("ads", raw.page_text)

    def test_broken_jsonld_is_ignored(self):
        page = '<html><head><script type="application/ld+json">{oops</script></head><body><h1>Пиріг</h1></body></html>'
        raw = extract_recipe(page, "https://example.com/pie/")
        self.assertEqual(raw.source, "text")
        self.assertEqual(raw.title, "Пиріг")


class EnsurePublicUrlTests(SimpleTestCase):
    def test_rejects_non_http_schemes(self):
        for url in ["file:///etc/passwd", "ftp://example.com/x", "javascript:alert(1)", "example.com"]:
            with self.assertRaises(ImportFailed):
                ensure_public_url(url)

    def test_rejects_loopback(self):
        with self.assertRaises(ImportFailed):
            ensure_public_url("http://127.0.0.1:8000/admin/")

    @patch("recipes.importing.fetch.socket.getaddrinfo", return_value=PRIVATE_ADDRESS)
    def test_rejects_private_network(self, _):
        with self.assertRaises(ImportFailed):
            ensure_public_url("https://internal.example.com/")

    @patch("recipes.importing.fetch.socket.getaddrinfo", return_value=PUBLIC_ADDRESS)
    def test_accepts_public_host(self, _):
        ensure_public_url("https://example.com/recipe/")


@patch("recipes.importing.fetch.socket.getaddrinfo", return_value=PUBLIC_ADDRESS)
class FetchPageTests(SimpleTestCase):
    def make_session(self, *responses):
        session = Mock()
        session.headers = {}
        session.get.side_effect = list(responses)
        return session

    def test_returns_html(self, _):
        page = "<html><body>Борщ</body></html>".encode("utf-8")
        session = self.make_session(response(status=404), response(body=page))

        fetched = fetch_page("https://example.com/borsch/", session=session)

        self.assertEqual(fetched.content, page)
        self.assertEqual(fetched.encoding, "utf-8")

    def test_respects_robots_txt(self, _):
        robots = response(content_type="text/plain", body=b"User-agent: *\nDisallow: /")
        with self.assertRaises(ImportFailed):
            fetch_page("https://example.com/borsch/", session=self.make_session(robots))

    def test_rejects_non_html(self, _):
        session = self.make_session(response(status=404), response(content_type="application/pdf"))
        with self.assertRaises(ImportFailed):
            fetch_page("https://example.com/recipe.pdf", session=session)

    def test_follows_redirect(self, _):
        session = self.make_session(
            response(status=404),
            response(status=301, location="/new-borsch/"),
            response(body=b"<html>ok</html>"),
        )
        fetched = fetch_page("https://example.com/borsch/", session=session)
        self.assertEqual(fetched.url, "https://example.com/new-borsch/")

    def test_rejects_redirect_to_private_address(self, getaddrinfo):
        getaddrinfo.side_effect = [PUBLIC_ADDRESS, PUBLIC_ADDRESS, PRIVATE_ADDRESS]
        session = self.make_session(response(status=404), response(status=302, location="http://10.0.0.5/"))
        with self.assertRaises(ImportFailed):
            fetch_page("https://example.com/borsch/", session=session)