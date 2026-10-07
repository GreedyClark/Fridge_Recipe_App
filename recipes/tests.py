from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from fridge.models import FridgeItem, Product

from .models import CookingLog, Recipe, RecipeIngredient
from .services import READY, match_recipes


class RecipeTestMixin:
    def setUp(self):
        self.owner = User.objects.create_user("owner", password="pass")
        self.stranger = User.objects.create_user("stranger", password="pass")
        self.eggs = Product.objects.create(name="Яйце", default_unit="pcs", calories_per_100=143, grams_per_piece=50)
        self.salt = Product.objects.create(name="Сіль", default_unit="g", calories_per_100=0)

    def make_recipe(self, owner=None, name="Яєчня"):
        recipe = Recipe.objects.create(name=name, instructions="Посмажити.", owner=owner)
        RecipeIngredient.objects.create(recipe=recipe, product=self.eggs, quantity=2)
        RecipeIngredient.objects.create(recipe=recipe, product=self.salt, quantity=None, optional=True)
        return recipe


class RecipeVisibilityTests(RecipeTestMixin, TestCase):
    def test_shared_recipe_visible_to_everyone(self):
        recipe = self.make_recipe()
        self.client.force_login(self.stranger)
        self.assertEqual(self.client.get(reverse("recipes:detail", args=[recipe.pk])).status_code, 200)

    def test_private_recipe_hidden_from_others(self):
        recipe = self.make_recipe(owner=self.owner)
        self.client.force_login(self.stranger)

        self.assertEqual(self.client.get(reverse("recipes:detail", args=[recipe.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("recipes:cook", args=[recipe.pk])).status_code, 404)
        names = [match.recipe.name for match in match_recipes(self.stranger)]
        self.assertNotIn(recipe.name, names)

    def test_private_recipe_visible_to_owner(self):
        recipe = self.make_recipe(owner=self.owner)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("recipes:detail", args=[recipe.pk])).status_code, 200)
        self.assertIn(recipe.name, [match.recipe.name for match in match_recipes(self.owner)])


class OptionalIngredientTests(RecipeTestMixin, TestCase):
    def test_missing_optional_does_not_block_cooking(self):
        recipe = self.make_recipe(owner=self.owner)
        FridgeItem.objects.create(user=self.owner, product=self.eggs, quantity=2)

        match = next(match for match in match_recipes(self.owner) if match.recipe == recipe)
        self.assertEqual(match.status, READY)

    def test_cooking_skips_optional_ingredients(self):
        recipe = self.make_recipe(owner=self.owner)
        FridgeItem.objects.create(user=self.owner, product=self.eggs, quantity=3)
        FridgeItem.objects.create(user=self.owner, product=self.salt, quantity=100)
        self.client.force_login(self.owner)

        self.client.post(reverse("recipes:cook", args=[recipe.pk]))

        self.assertEqual(CookingLog.objects.filter(user=self.owner).count(), 1)
        self.assertEqual(FridgeItem.objects.get(product=self.eggs).quantity, 1)
        self.assertEqual(FridgeItem.objects.get(product=self.salt).quantity, 100)

    def test_nutrition_ignores_ingredients_without_quantity(self):
        recipe = self.make_recipe()
        self.assertEqual(recipe.nutrition()["calories"], 143)


class MyRecipesTests(RecipeTestMixin, TestCase):
    def test_mine_filter_shows_only_own_recipes(self):
        self.make_recipe(name="Спільна яєчня")
        self.make_recipe(owner=self.owner, name="Моя яєчня")
        self.client.force_login(self.owner)

        response = self.client.get(reverse("recipes:list"), {"filter": "mine"})

        names = [match.recipe.name for match in response.context["matches"]]
        self.assertEqual(names, ["Моя яєчня"])
        self.assertContains(response, "Мій")

    def test_mine_filter_empty_state_offers_import(self):
        self.client.force_login(self.stranger)
        response = self.client.get(reverse("recipes:list"), {"filter": "mine"})
        self.assertContains(response, "Своїх рецептів поки немає")
        self.assertContains(response, reverse("recipes:import"))

    def test_detail_shows_source_photo_and_optional_group(self):
        recipe = self.make_recipe(owner=self.owner)
        recipe.image_url = "https://example.com/photo.jpg"
        recipe.source_url = "https://www.example.com/recipe/"
        recipe.save()
        self.client.force_login(self.owner)

        response = self.client.get(reverse("recipes:detail", args=[recipe.pk]))

        self.assertContains(response, 'src="https://example.com/photo.jpg"')
        self.assertContains(response, "Фото: example.com")
        self.assertContains(response, "За смаком")
        self.assertContains(response, reverse("recipes:delete", args=[recipe.pk]))

    def test_owner_deletes_recipe_and_log_stays(self):
        recipe = self.make_recipe(owner=self.owner)
        CookingLog.objects.create(user=self.owner, recipe=recipe, calories_consumed=143)
        self.client.force_login(self.owner)

        response = self.client.post(reverse("recipes:delete", args=[recipe.pk]))

        self.assertRedirects(response, reverse("recipes:list"))
        self.assertFalse(Recipe.objects.filter(pk=recipe.pk).exists())
        self.assertIsNone(CookingLog.objects.get().recipe)

    def test_cannot_delete_shared_or_foreign_recipe(self):
        shared = self.make_recipe(name="Спільна")
        foreign = self.make_recipe(owner=self.owner, name="Чужа")
        self.client.force_login(self.stranger)

        for recipe in (shared, foreign):
            self.assertEqual(self.client.post(reverse("recipes:delete", args=[recipe.pk])).status_code, 404)
        self.assertEqual(Recipe.objects.count(), 2)