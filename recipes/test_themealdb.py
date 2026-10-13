from io import StringIO
from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from fridge.models import Product

from .importing.errors import ImportFailed, QuotaExceeded
from .importing.gemini import build_prompt
from .importing.themealdb import iter_meals, meal_to_raw
from .models import Recipe, RecipeImport

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


class SharedDraftTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("admin", password="pass", is_staff=True)
        self.importer = User.objects.create_user("bot", password="pass", is_staff=True)
        self.cook = User.objects.create_user("cook", password="pass")
        self.buckwheat = Product.objects.create(name="Гречка", default_unit="g", calories_per_100=346)
        self.private = Product.objects.create(name="Мій соус", default_unit="g", calories_per_100=100, owner=self.admin)
        self.draft = RecipeImport.objects.create(
            user=self.importer,
            url="https://www.themealdb.com/meal/1",
            external_id="themealdb:1",
            data={
                "name": "Гречка з борошном",
                "original_name": "Buckwheat",
                "meal_type": "lunch",
                "servings": 2,
                "steps": ["Зварити гречку."],
                "ingredients": [
                    {"original": "200g Buckwheat", "product_id": self.buckwheat.pk, "new_product_name": "",
                     "usda_query": "", "unit": "g", "quantity": 200.0, "grams_per_piece": None,
                     "optional": False, "warning": ""},
                    {"original": "100g Flour", "product_id": None, "new_product_name": "Борошно",
                     "usda_query": "Wheat flour", "unit": "g", "quantity": 100.0, "grams_per_piece": None,
                     "optional": False, "warning": "Перевір кількість"},
                ],
                "image_url": "https://www.themealdb.com/images/1.jpg",
                "source_url": "https://www.themealdb.com/meal/1",
            },
        )
        self.review_url = reverse("recipes:import_review", args=[self.draft.pk])

    def form_data(self, **changes):
        data = {
            "name": "Гречка з борошном",
            "meal_type": "lunch",
            "servings": "2",
            "steps": ["Зварити гречку."],
            "ingredients-TOTAL_FORMS": "2",
            "ingredients-INITIAL_FORMS": "2",
            "ingredients-MIN_NUM_FORMS": "0",
            "ingredients-MAX_NUM_FORMS": "1000",
            "ingredients-0-product": str(self.buckwheat.pk),
            "ingredients-0-quantity": "200",
            "ingredients-1-product": "",
            "ingredients-1-new_product_name": "Борошно",
            "ingredients-1-usda_query": "Wheat flour",
            "ingredients-1-new_unit": "g",
            "ingredients-1-quantity": "100",
        }
        data.update(changes)
        return data

    def fake_import(self, name, query, unit, grams_per_piece=None, owner=None, **kwargs):
        product = Product.objects.create(name=name, default_unit=unit, calories_per_100=364, owner=owner)
        return product, True, {}

    def test_drafts_page_for_staff(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("recipes:drafts"))

        self.assertContains(response, "Гречка з борошном")
        self.assertContains(response, "Оригінал: Buckwheat")
        self.assertContains(response, "Перевір 1 рядок")
        self.assertContains(response, "1 чернетка")

    def test_drafts_page_forbidden_for_users(self):
        self.client.force_login(self.cook)

        self.assertEqual(self.client.get(reverse("recipes:drafts")).status_code, 403)
        self.assertEqual(self.client.get(self.review_url).status_code, 404)

    def test_drafts_button_only_for_staff(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("recipes:list")), reverse("recipes:drafts"))

        self.client.force_login(self.cook)
        self.assertNotContains(self.client.get(reverse("recipes:list")), reverse("recipes:drafts"))

    def test_review_offers_shared_catalog_only(self):
        self.client.force_login(self.admin)

        response = self.client.get(self.review_url)

        self.assertContains(response, "Спільний рецепт")
        self.assertContains(response, 'name="shared"')
        self.assertContains(response, "checked")
        self.assertNotContains(response, "Мій соус")

    @patch("recipes.importing.save.import_product")
    def test_saves_shared_recipe(self, import_product):
        import_product.side_effect = self.fake_import
        self.client.force_login(self.admin)

        response = self.client.post(self.review_url, self.form_data(shared="on"))

        recipe = Recipe.objects.get()
        self.assertRedirects(response, reverse("recipes:detail", args=[recipe.pk]))
        self.assertIsNone(recipe.owner)
        self.assertIsNone(Product.objects.get(name="Борошно").owner)
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.status, RecipeImport.SAVED)

        self.client.force_login(self.cook)
        detail = self.client.get(reverse("recipes:detail", args=[recipe.pk]))
        self.assertContains(detail, "Дані й фото:")
        self.assertContains(detail, "TheMealDB")

    @patch("recipes.importing.save.import_product")
    def test_unchecked_shared_saves_private(self, import_product):
        import_product.side_effect = self.fake_import
        self.client.force_login(self.admin)

        self.client.post(self.review_url, self.form_data())

        self.assertEqual(Recipe.objects.get().owner, self.importer)

    def test_private_product_rejected_in_shared_draft(self):
        self.client.force_login(self.admin)

        response = self.client.post(self.review_url, self.form_data(**{"ingredients-0-product": str(self.private.pk)}))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Recipe.objects.exists())

    @patch("recipes.importing.save.import_product")
    def test_users_cannot_share(self, import_product):
        import_product.side_effect = self.fake_import
        record = RecipeImport.objects.create(user=self.cook, url="https://example.com/r/", data=self.draft.data)
        self.client.force_login(self.cook)

        page = self.client.get(reverse("recipes:import_review", args=[record.pk]))
        self.client.post(reverse("recipes:import_review", args=[record.pk]), self.form_data(shared="on"))

        self.assertNotContains(page, "Спільний рецепт")
        self.assertEqual(Recipe.objects.get().owner, self.cook)

    def test_reject_draft(self):
        self.client.force_login(self.admin)

        response = self.client.post(reverse("recipes:reject_draft", args=[self.draft.pk]))

        self.assertRedirects(response, reverse("recipes:drafts"))
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.status, RecipeImport.REJECTED)
        self.assertNotContains(self.client.get(reverse("recipes:drafts")), "Гречка з борошном")

    def test_reject_forbidden_for_users(self):
        self.client.force_login(self.cook)

        response = self.client.post(reverse("recipes:reject_draft", args=[self.draft.pk]))

        self.assertEqual(response.status_code, 403)
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.status, RecipeImport.DRAFT)

    def test_no_attribution_for_other_sources(self):
        recipe = Recipe.objects.create(name="Каша", instructions="Зварити", source_url="https://example.com/k/")
        self.client.force_login(self.cook)

        response = self.client.get(reverse("recipes:detail", args=[recipe.pk]))

        self.assertNotContains(response, "Дані й фото:")
