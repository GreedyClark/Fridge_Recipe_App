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