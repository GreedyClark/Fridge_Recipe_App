import json
import socket
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, TestCase, override_settings
from google.genai import errors as genai_errors

from fridge.models import Product

from .importing.errors import ImportFailed
from .importing.extract import RawRecipe, extract_recipe
from .importing.fetch import ensure_public_url, fetch_page
from .importing.gemini import CHECK_QUANTITY, CHOOSE_PRODUCT, parse_recipe

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

class GeminiParsingTests(TestCase):
    def setUp(self):
        self.buckwheat = Product.objects.create(name="Гречка", default_unit="g", calories_per_100=346)
        self.eggs = Product.objects.create(name="Яйце куряче", default_unit="pcs", calories_per_100=143, grams_per_piece=50)
        self.raw = RawRecipe(url="https://example.com/r/", source="text", title="Гречаники", image_url="https://example.com/a.jpg", page_text="...")

    def gemini_returns(self, payload):
        client = Mock()
        client.models.generate_content.return_value = Mock(text=json.dumps(payload))
        return client

    def payload(self, ingredients, **extra):
        data = {"is_recipe": True, "name": "Гречаники", "servings": 2, "steps": ["Зварити гречку.", " "], "ingredients": ingredients}
        data.update(extra)
        return data

    @override_settings(GEMINI_API_KEY="key", GEMINI_MODEL="model")
    def test_builds_draft(self):
        client = self.gemini_returns(self.payload([
            {"original": "1 склянка гречки", "product_id": self.buckwheat.pk, "unit": "g", "quantity": 165},
            {"original": "2 яйця", "product_id": self.eggs.pk, "unit": "pcs", "quantity": 2},
            {"original": "200 г сиру фета", "new_product_name": "Сир фета", "usda_query": "Cheese, feta", "unit": "g", "quantity": 200},
            {"original": "сіль за смаком", "new_product_name": "Сіль", "usda_query": "Salt, table", "unit": "g", "optional": True},
        ]))

        draft = parse_recipe(self.raw, client=client)

        self.assertEqual(draft["name"], "Гречаники")
        self.assertEqual(draft["servings"], 2)
        self.assertEqual(draft["steps"], ["Зварити гречку."])
        self.assertEqual(draft["image_url"], "https://example.com/a.jpg")
        rows = draft["ingredients"]
        self.assertEqual([row["warning"] for row in rows], ["", "", "", ""])
        self.assertEqual(rows[0]["product_id"], self.buckwheat.pk)
        self.assertEqual(rows[2]["new_product_name"], "Сир фета")
        self.assertTrue(rows[3]["optional"])
        self.assertIsNone(rows[3]["quantity"])
        prompt = client.models.generate_content.call_args.kwargs["contents"]
        self.assertIn(f"{self.eggs.pk} | Яйце куряче | pcs | 50", prompt)

    @override_settings(GEMINI_API_KEY="key", GEMINI_MODEL="model")
    def test_flags_suspicious_rows(self):
        client = self.gemini_returns(self.payload([
            {"original": "яйця", "product_id": self.eggs.pk, "unit": "g", "quantity": 100},
            {"original": "щось", "product_id": 99999, "unit": "g", "quantity": 10},
            {"original": "авокадо", "new_product_name": "Авокадо", "usda_query": "Avocados, raw", "unit": "pcs", "quantity": 1},
            {"original": "гречка", "product_id": self.buckwheat.pk, "unit": "g", "quantity": -5},
        ], servings=500))

        draft = parse_recipe(self.raw, client=client)

        self.assertEqual(draft["servings"], 1)
        rows = draft["ingredients"]
        self.assertEqual(rows[0]["warning"], CHECK_QUANTITY)
        self.assertEqual(rows[0]["unit"], "pcs")
        self.assertEqual(rows[1]["warning"], CHOOSE_PRODUCT)
        self.assertEqual(rows[2]["warning"], CHOOSE_PRODUCT)
        self.assertEqual(rows[3]["warning"], CHECK_QUANTITY)

    @override_settings(GEMINI_API_KEY="key", GEMINI_MODEL="model")
    def test_merges_duplicate_products(self):
        client = self.gemini_returns(self.payload([
            {"original": "2 яйця для тіста", "product_id": self.eggs.pk, "unit": "pcs", "quantity": 2},
            {"original": "1 яйце для змащування", "product_id": self.eggs.pk, "unit": "pcs", "quantity": 1},
        ]))
        rows = parse_recipe(self.raw, client=client)["ingredients"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["quantity"], 3)

    @override_settings(GEMINI_API_KEY="key", GEMINI_MODEL="model")
    def test_not_a_recipe(self):
        client = self.gemini_returns({"is_recipe": False, "name": "", "servings": 1, "steps": [], "ingredients": []})
        with self.assertRaises(ImportFailed):
            parse_recipe(self.raw, client=client)

    @override_settings(GEMINI_API_KEY="key", GEMINI_MODEL="model")
    def test_invalid_json(self):
        client = Mock()
        client.models.generate_content.return_value = Mock(text="це не JSON")
        with self.assertRaises(ImportFailed):
            parse_recipe(self.raw, client=client)

    @override_settings(GEMINI_API_KEY="key", GEMINI_MODEL="model")
    def test_quota_error(self):
        client = Mock()
        client.models.generate_content.side_effect = genai_errors.ClientError(429, {"error": {"message": "quota"}})
        with self.assertRaisesMessage(ImportFailed, "ліміт"):
            parse_recipe(self.raw, client=client)

    @override_settings(GEMINI_API_KEY="key", GEMINI_MODEL="model")
    def test_overloaded(self):
        client = Mock()
        client.models.generate_content.side_effect = genai_errors.ServerError(503, {"error": {"message": "high demand"}})
        with self.assertRaisesMessage(ImportFailed, "перевантажений"):
            parse_recipe(self.raw, client=client)

    @override_settings(GEMINI_API_KEY="", GEMINI_MODEL="model")
    def test_missing_key(self):
        with self.assertRaises(ImportFailed):
            parse_recipe(self.raw, client=Mock())


    @override_settings(GEMINI_API_KEY="key", GEMINI_MODEL="main", GEMINI_FALLBACK_MODELS=["lite"])
    def test_falls_back_to_next_model(self):
        client = self.gemini_returns(self.payload([
            {"original": "2 яйця", "product_id": self.eggs.pk, "unit": "pcs", "quantity": 2},
        ]))
        answer = client.models.generate_content.return_value
        client.models.generate_content.side_effect = [
            genai_errors.ServerError(503, {"error": {"message": "high demand"}}),
            answer,
        ]

        draft = parse_recipe(self.raw, client=client)

        self.assertEqual(draft["ingredients"][0]["quantity"], 2)
        models = [call.kwargs["model"] for call in client.models.generate_content.call_args_list]
        self.assertEqual(models, ["main", "lite"])

    @override_settings(GEMINI_API_KEY="key", GEMINI_MODEL="main", GEMINI_FALLBACK_MODELS=["lite"])
    def test_all_models_overloaded(self):
        client = Mock()
        client.models.generate_content.side_effect = genai_errors.ServerError(503, {"error": {"message": "high demand"}})
        with self.assertRaisesMessage(ImportFailed, "перевантажений"):
            parse_recipe(self.raw, client=client)
        self.assertEqual(client.models.generate_content.call_count, 2)


    @override_settings(GEMINI_API_KEY="key", GEMINI_MODEL="model")
    def test_water_is_always_optional(self):
        client = self.gemini_returns(self.payload([
            {"original": "200 мл води", "new_product_name": "Вода", "usda_query": "Water, tap", "unit": "ml", "quantity": 200},
        ]))
        row = parse_recipe(self.raw, client=client)["ingredients"][0]
        self.assertTrue(row["optional"])
        self.assertEqual(row["quantity"], 200)