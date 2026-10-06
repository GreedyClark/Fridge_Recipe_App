from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

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