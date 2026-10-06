from unittest.mock import Mock

import requests
from django.test import TestCase, override_settings

from .models import Product
from .usda import ENERGY_NUMBERS, USDAError, choose_food, clean_query, extract_nutrients, import_product, pick

SEARCH_FOOD = {
    "fdcId": 173944,
    "description": "Bananas, raw",
    "foodNutrients": [
        {"nutrientNumber": "208", "unitName": "KCAL", "value": 89.0},
        {"nutrientNumber": "268", "unitName": "kJ", "value": 371.0},
        {"nutrientNumber": "203", "unitName": "G", "value": 1.09},
        {"nutrientNumber": "204", "unitName": "G", "value": 0.33},
        {"nutrientNumber": "205", "unitName": "G", "value": 22.84},
    ],
}

DETAIL_FOOD = {
    "fdcId": 2346406,
    "description": "Cucumber, with peel, raw",
    "foodNutrients": [
        {"nutrient": {"number": "957", "unitName": "kcal"}, "amount": 15.9},
        {"nutrient": {"number": "203", "unitName": "g"}, "amount": 0.62},
    ],
}


def fake_response(payload):
    response = Mock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


class NutrientParsingTests(TestCase):
    def test_search_format(self):
        nutrients = extract_nutrients(SEARCH_FOOD)
        self.assertEqual(pick(nutrients, ENERGY_NUMBERS, unit="KCAL"), 89.0)
        self.assertEqual(pick(nutrients, ("205",)), 22.8)

    def test_detail_format_with_atwater_energy(self):
        nutrients = extract_nutrients(DETAIL_FOOD)
        self.assertEqual(pick(nutrients, ENERGY_NUMBERS, unit="KCAL"), 15.9)
        self.assertIsNone(pick(nutrients, ("204",)))

    def test_energy_in_kilojoules_is_ignored(self):
        food = {"foodNutrients": [{"nutrientNumber": "208", "unitName": "kJ", "value": 371.0}]}
        self.assertIsNone(pick(extract_nutrients(food), ENERGY_NUMBERS, unit="KCAL"))


class ChooseFoodTests(TestCase):
    def test_exact_match_wins(self):
        foods = [{"description": "Bananas, dried"}, {"description": "Bananas, raw"}]
        self.assertEqual(choose_food(foods, "Bananas, raw")["description"], "Bananas, raw")

    def test_raw_preferred_over_cooked(self):
        foods = [{"description": "Beef, chuck, cooked, braised"}, {"description": "Beef, chuck, raw"}]
        self.assertEqual(choose_food(foods, "Beef, chuck, all grades, raw")["description"], "Beef, chuck, raw")

    def test_first_result_as_fallback(self):
        foods = [{"description": "Bologna, pork"}, {"description": "Bologna, turkey"}]
        self.assertEqual(choose_food(foods, "Bologna, beef")["description"], "Bologna, pork")

    def test_clean_query_removes_unsafe_chars(self):
        self.assertEqual(clean_query('Beef, 80% lean / 20% fat (raw) 0"'), "Beef, 80% lean 20% fat raw 0")


@override_settings(USDA_API_KEY="test-key")
class ImportProductTests(TestCase):
    def test_creates_product_from_search(self):
        session = Mock()
        session.get.return_value = fake_response({"foods": [SEARCH_FOOD]})

        product, created, food = import_product("Банан", "Bananas, raw", "pcs", grams_per_piece=120, session=session)

        self.assertTrue(created)
        self.assertEqual(product.calories_per_100, 89.0)
        self.assertEqual(product.grams_per_piece, 120)
        self.assertEqual(product.usda_fdc_id, 173944)

    def test_updates_existing_product(self):
        Product.objects.create(name="Банан", default_unit="pcs", calories_per_100=1, grams_per_piece=120)
        session = Mock()
        session.get.return_value = fake_response({"foods": [SEARCH_FOOD]})

        product, created, _ = import_product("Банан", "Bananas, raw", "pcs", grams_per_piece=120, session=session)

        self.assertFalse(created)
        self.assertEqual(Product.objects.get(name="Банан").calories_per_100, 89.0)

    def test_not_found_raises(self):
        session = Mock()
        session.get.return_value = fake_response({"foods": []})
        with self.assertRaises(USDAError):
            import_product("Щось", "Nothing at all", "g", session=session)

    def test_network_error_raises(self):
        session = Mock()
        session.get.side_effect = requests.ConnectionError("no network")
        with self.assertRaises(USDAError):
            import_product("Банан", "Bananas, raw", "pcs", session=session)
        self.assertFalse(Product.objects.exists())

    @override_settings(USDA_API_KEY="")
    def test_missing_key_raises(self):
        with self.assertRaises(USDAError):
            import_product("Банан", "Bananas, raw", "pcs")