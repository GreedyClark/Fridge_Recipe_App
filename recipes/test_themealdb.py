from io import StringIO
from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase

from .importing.errors import ImportFailed, QuotaExceeded
from .importing.gemini import build_prompt
from .importing.themealdb import iter_meals, meal_to_raw
from .models import RecipeImport

COMMAND = "recipes.management.commands.import_themealdb"


def meal(meal_id, name="Pancakes", category="Breakfast"):
    return {
        "idMeal": meal_id,
        "strMeal": name,
        "strCategory": category,
        "strInstructions": "STEP 1\r\nMix flour and eggs.\r\n\r\n2.\r\nFry in a pan.",
        "strMealThumb": f"https://www.themealdb.com/images/media/meals/{meal_id}.jpg",
        "strIngredient1": "Flour",
        "strMeasure1": "100g",
        "strIngredient2": "Eggs",
        "strMeasure2": "2",
        "strIngredient3": "Salt",
        "strMeasure3": " ",
        "strIngredient4": "",
        "strMeasure4": "",
        "strIngredient5": None,
    }


def api_response(meals):
    response = Mock()
    response.json.return_value = {"meals": meals}
    response.raise_for_status.return_value = None
    return response


def draft(name):
    return {"name": name, "meal_type": "breakfast", "servings": 2, "steps": ["Крок"], "ingredients": []}


class MealToRawTests(SimpleTestCase):
    def test_converts_meal(self):
        raw = meal_to_raw(meal("52772"))

        self.assertEqual(raw.url, "https://www.themealdb.com/meal/52772")
        self.assertEqual(raw.title, "Pancakes")
        self.assertEqual(raw.category, "Breakfast")
        self.assertEqual(raw.ingredients, ["100g Flour", "2 Eggs", "Salt"])
        self.assertEqual(raw.instructions, ["Mix flour and eggs.", "Fry in a pan."])
        self.assertTrue(raw.image_url.endswith("52772.jpg"))

    def test_category_in_prompt(self):
        prompt = build_prompt(meal_to_raw(meal("1")), [])

        self.assertIn("Категорія на сайті: Breakfast", prompt)
        self.assertIn("- 100g Flour", prompt)

    def test_category_uses_lookup(self):
        session = Mock()
        session.get.side_effect = [
            api_response([{"idMeal": "1", "strMeal": "Pancakes"}]),
            api_response([meal("1")]),
        ]

        meals = list(iter_meals(session, "Breakfast"))

        self.assertEqual([item["idMeal"] for item in meals], ["1"])
        self.assertIn("filter.php", session.get.call_args_list[0].args[0])
        self.assertIn("lookup.php", session.get.call_args_list[1].args[0])

    def test_search_by_letters(self):
        session = Mock()
        session.get.return_value = api_response(None)

        self.assertEqual(list(iter_meals(session)), [])
        self.assertEqual(session.get.call_count, 26)


@patch(f"{COMMAND}.time.sleep")
@patch(f"{COMMAND}.iter_meals")
@patch(f"{COMMAND}.parse_recipe")
class ImportCommandTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("admin", password="pass", is_staff=True)

    def run_command(self, *args):
        out = StringIO()
        call_command("import_themealdb", *args, stdout=out)
        return out.getvalue()

    def test_creates_drafts_for_admin(self, parse, meals, sleep):
        meals.return_value = iter([meal("1", "Pancakes"), meal("2", "Omelette")])
        parse.side_effect = [draft("Млинці"), draft("Омлет")]

        output = self.run_command("--limit", "5")

        records = RecipeImport.objects.order_by("external_id")
        self.assertEqual([r.external_id for r in records], ["themealdb:1", "themealdb:2"])
        self.assertTrue(all(r.user == self.admin and r.status == RecipeImport.DRAFT for r in records))
        self.assertEqual(records[0].url, "https://www.themealdb.com/meal/1")
        self.assertEqual(records[0].data["name"], "Млинці")
        self.assertIn("Нових чернеток: 2", output)
        self.assertEqual(sleep.call_count, 1)

    def test_skips_imported_and_respects_limit(self, parse, meals, sleep):
        RecipeImport.objects.create(user=self.admin, url="https://www.themealdb.com/meal/1", external_id="themealdb:1")
        meals.return_value = iter([meal("1"), meal("2"), meal("3")])
        parse.return_value = draft("Страва")

        self.run_command("--limit", "1")

        self.assertEqual(parse.call_count, 1)
        self.assertTrue(RecipeImport.objects.filter(external_id="themealdb:2").exists())
        self.assertFalse(RecipeImport.objects.filter(external_id="themealdb:3").exists())

    def test_stops_on_quota(self, parse, meals, sleep):
        meals.return_value = iter([meal("1"), meal("2"), meal("3")])
        parse.side_effect = [draft("Перша"), QuotaExceeded("Вичерпано ліміт запитів до Gemini.")]

        output = self.run_command()

        self.assertEqual(parse.call_count, 2)
        self.assertEqual(RecipeImport.objects.count(), 1)
        self.assertIn("Вичерпано ліміт", output)
        self.assertIn("Нових чернеток: 1", output)

    def test_skips_failed_meal(self, parse, meals, sleep):
        meals.return_value = iter([meal("1", "Bad"), meal("2", "Good")])
        parse.side_effect = [ImportFailed("Не вдалося знайти рецепт на сторінці."), draft("Добра")]

        output = self.run_command()

        self.assertEqual(list(RecipeImport.objects.values_list("external_id", flat=True)), ["themealdb:2"])
        self.assertIn("Bad", output)
        self.assertIn("пропущено: 1", output)

    def test_api_error(self, parse, meals, sleep):
        meals.side_effect = ImportFailed("TheMealDB не відповідає. Спробуй пізніше.")

        output = self.run_command()

        self.assertIn("TheMealDB не відповідає", output)
        self.assertEqual(RecipeImport.objects.count(), 0)

    def test_requires_admin(self, parse, meals, sleep):
        self.admin.is_staff = False
        self.admin.save()

        with self.assertRaises(CommandError):
            self.run_command()
