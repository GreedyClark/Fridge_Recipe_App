from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from fridge.models import Product
from fridge.usda import USDAError

from .importing.errors import ImportFailed
from .models import Recipe, RecipeImport

DRAFT = {
    "name": "Гречаники",
    "servings": 2,
    "steps": ["Зварити гречку."],
    "ingredients": [
        {"original": "1 склянка гречки", "product_id": None, "new_product_name": "Гречка", "usda_query": "Buckwheat",
         "unit": "g", "quantity": 165, "grams_per_piece": None, "optional": False, "warning": ""},
    ],
    "image_url": "",
    "source_url": "https://example.com/r/",
}


class RecipeImportViewTests(TestCase):
    url = "https://example.com/r/"

    def setUp(self):
        self.user = User.objects.create_user("cook", password="pass12345")
        self.client.force_login(self.user)

    def post(self, url=None):
        return self.client.post(reverse("recipes:import"), {"url": url or self.url})

    def test_page_opens(self):
        response = self.client.get(reverse("recipes:import"))
        self.assertContains(response, "Розібрати рецепт")

    @patch("recipes.views.run_import")
    def test_success_redirects_to_review(self, run_import):
        def fake_run(record):
            record.data = DRAFT
            record.save()

        run_import.side_effect = fake_run

        response = self.post()

        record = RecipeImport.objects.get()
        self.assertRedirects(response, reverse("recipes:import_review", args=[record.pk]))
        review = self.client.get(response.url)
        self.assertContains(review, "Гречаники")
        self.assertContains(review, "1 склянка гречки")

    @patch("recipes.views.run_import", side_effect=ImportFailed("Сайт не відповідає. Спробуй пізніше."))
    def test_failure_shows_error(self, run_import):
        response = self.post()
        self.assertContains(response, "Сайт не відповідає")

    @patch("recipes.importing.pipeline.fetch_page", side_effect=ImportFailed("Сайт не відповідає."))
    def test_failure_is_recorded(self, fetch_page):
        self.post()
        record = RecipeImport.objects.get()
        self.assertEqual(record.status, RecipeImport.FAILED)
        self.assertEqual(record.error, "Сайт не відповідає.")

    @override_settings(IMPORTS_PER_DAY=1)
    @patch("recipes.views.run_import")
    def test_daily_limit(self, run_import):
        RecipeImport.objects.create(user=self.user, url="https://example.com/other/", status=RecipeImport.FAILED)
        response = self.post()
        self.assertContains(response, "ліміт вичерпано")
        run_import.assert_not_called()

    @patch("recipes.views.run_import")
    def test_existing_draft_is_reused(self, run_import):
        draft = RecipeImport.objects.create(user=self.user, url=self.url, data=DRAFT)
        response = self.post()
        self.assertRedirects(response, reverse("recipes:import_review", args=[draft.pk]))
        run_import.assert_not_called()

    @patch("recipes.views.run_import")
    def test_existing_recipe_is_opened(self, run_import):
        recipe = Recipe.objects.create(name="Гречаники", owner=self.user, source_url=self.url)
        response = self.post()
        self.assertRedirects(response, reverse("recipes:detail", args=[recipe.pk]))
        run_import.assert_not_called()

    def test_review_of_other_user_is_hidden(self):
        other = User.objects.create_user("other", password="pass12345")
        draft = RecipeImport.objects.create(user=other, url=self.url, data=DRAFT)
        response = self.client.get(reverse("recipes:import_review", args=[draft.pk]))
        self.assertEqual(response.status_code, 404)

class ImportReviewViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("cook", password="pass12345")
        self.client.force_login(self.user)
        self.buckwheat = Product.objects.create(name="Гречка", default_unit="g", calories_per_100=346)
        self.eggs = Product.objects.create(name="Яйце куряче", default_unit="pcs", calories_per_100=143,
                                           grams_per_piece=50)
        self.record = RecipeImport.objects.create(user=self.user, url="https://example.com/r/", data={
            "name": "Гречаники",
            "servings": 2,
            "steps": ["Зварити гречку.", "Сформувати котлети."],
            "ingredients": [
                {"original": "1 склянка гречки", "product_id": self.buckwheat.pk, "new_product_name": "",
                 "usda_query": "",
                 "unit": "g", "quantity": 165.0, "grams_per_piece": None, "optional": False, "warning": ""},
                {"original": "2 яйця", "product_id": self.eggs.pk, "new_product_name": "", "usda_query": "",
                 "unit": "pcs", "quantity": 2.0, "grams_per_piece": None, "optional": False,
                 "warning": "Перевір кількість"},
                {"original": "500 г борошна", "product_id": None, "new_product_name": "Борошно",
                 "usda_query": "Wheat flour, white, all-purpose", "unit": "g", "quantity": 500.0,
                 "grams_per_piece": None, "optional": False, "warning": ""},
            ],
            "image_url": "https://example.com/a.jpg",
            "source_url": "https://example.com/r/",
        })
        self.url = reverse("recipes:import_review", args=[self.record.pk])

    def form_data(self, **changes):
        data = {
            "name": "Гречаники",
            "servings": "2",
            "steps": ["Зварити гречку.", "", "Сформувати котлети."],
            "ingredients-TOTAL_FORMS": "3",
            "ingredients-INITIAL_FORMS": "3",
            "ingredients-MIN_NUM_FORMS": "0",
            "ingredients-MAX_NUM_FORMS": "1000",
            "ingredients-0-original": "1 склянка гречки",
            "ingredients-0-product": str(self.buckwheat.pk),
            "ingredients-0-quantity": "165",
            "ingredients-1-original": "2 яйця",
            "ingredients-1-product": str(self.eggs.pk),
            "ingredients-1-quantity": "2",
            "ingredients-2-original": "500 г борошна",
            "ingredients-2-product": "",
            "ingredients-2-new_product_name": "Борошно",
            "ingredients-2-usda_query": "Wheat flour, white, all-purpose",
            "ingredients-2-new_unit": "g",
            "ingredients-2-quantity": "500",
        }
        data.update(changes)
        return data

    def fake_import(self, name, query, unit, grams_per_piece=None, **kwargs):
        product = Product.objects.create(name=name, default_unit=unit, calories_per_100=364, usda_query=query)
        return product, True, {}

    def test_page_shows_draft(self):
        response = self.client.get(self.url)
        self.assertContains(response, "Перевір рецепт")
        self.assertContains(response, "Новий продукт: Борошно")
        self.assertContains(response, "Рядків, що потребують уваги: 1")
        self.assertContains(response, "Фото: example.com")
        self.assertContains(response, 'value="165"')

    @patch("recipes.importing.save.import_product")
    def test_saves_private_recipe(self, import_product):
        import_product.side_effect = self.fake_import

        response = self.client.post(self.url, self.form_data())

        recipe = Recipe.objects.get()
        self.assertRedirects(response, reverse("recipes:detail", args=[recipe.pk]))
        self.assertEqual(recipe.owner, self.user)
        self.assertEqual(recipe.instructions, "Зварити гречку.\nСформувати котлети.")
        self.assertEqual(recipe.image_url, "https://example.com/a.jpg")
        self.assertEqual(recipe.source_url, "https://example.com/r/")
        names = sorted(ingredient.product.name for ingredient in recipe.ingredients.all())
        self.assertEqual(names, ["Борошно", "Гречка", "Яйце куряче"])
        self.record.refresh_from_db()
        self.assertEqual(self.record.status, RecipeImport.SAVED)
        self.assertEqual(self.record.recipe, recipe)

    @patch("recipes.importing.save.import_product")
    def test_existing_product_is_reused(self, import_product):
        Product.objects.create(name="Борошно", default_unit="g", calories_per_100=364)
        self.client.post(self.url, self.form_data())
        import_product.assert_not_called()
        self.assertEqual(Product.objects.filter(name="Борошно").count(), 1)

    @patch("recipes.importing.save.import_product")
    def test_deleted_row_is_skipped(self, import_product):
        self.client.post(self.url, self.form_data(**{"ingredients-2-DELETE": "on"}))
        import_product.assert_not_called()
        self.assertEqual(Recipe.objects.get().ingredients.count(), 2)

    def test_quantity_required_unless_optional(self):
        response = self.client.post(self.url, self.form_data(**{"ingredients-0-quantity": ""}))
        self.assertContains(response, "Вкажи кількість")
        self.assertFalse(Recipe.objects.exists())

        self.client.post(self.url, self.form_data(**{
            "ingredients-0-quantity": "", "ingredients-0-optional": "on",
            "ingredients-2-product": str(self.eggs.pk),
            "ingredients-1-DELETE": "on",
        }))
        self.assertTrue(Recipe.objects.exists())

    def test_duplicate_products_rejected(self):
        response = self.client.post(self.url,
                                    self.form_data(**{"ingredients-2-product": str(self.buckwheat.pk)}))
        self.assertContains(response, "вказано двічі")
        self.assertFalse(Recipe.objects.exists())

    @patch("recipes.importing.save.import_product", side_effect=USDAError("нічого не знайдено"))
    def test_usda_failure_shows_row_error(self, import_product):
        response = self.client.post(self.url, self.form_data())
        self.assertContains(response, "Не вдалося знайти «Борошно» у базі USDA")
        self.assertFalse(Recipe.objects.exists())

    def test_steps_required(self):
        response = self.client.post(self.url, self.form_data(steps=["", " "]))
        self.assertContains(response, "Додай хоча б один крок")

    def test_saved_draft_is_closed(self):
        self.record.status = RecipeImport.SAVED
        self.record.save()
        self.assertEqual(self.client.get(self.url).status_code, 404)
