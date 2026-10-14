from datetime import date, timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Profile

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



class RecipeFormMixin(RecipeTestMixin):
    def setUp(self):
        super().setUp()
        self.recipe = self.make_recipe(owner=self.owner)
        self.url = reverse("recipes:edit", args=[self.recipe.pk])
        self.milk = Product.objects.create(name="Молоко", default_unit="ml", calories_per_100=52)

    def form_data(self, **changes):
        data = {
            "name": "Яєчня з молоком",
            "servings": "2",
            "steps": ["Збити яйця з молоком.", "Посмажити."],
            "ingredients-TOTAL_FORMS": "3",
            "ingredients-INITIAL_FORMS": "2",
            "ingredients-MIN_NUM_FORMS": "0",
            "ingredients-MAX_NUM_FORMS": "1000",
            "ingredients-0-product": str(self.eggs.pk),
            "ingredients-0-quantity": "3",
            "ingredients-1-product": str(self.salt.pk),
            "ingredients-1-optional": "on",
            "ingredients-1-DELETE": "on",
            "ingredients-2-product": str(self.milk.pk),
            "ingredients-2-quantity": "50",
        }
        data.update(changes)
        return data


class RecipeEditTests(RecipeFormMixin, TestCase):
    def test_edit_page_shows_current_recipe(self):
        self.client.force_login(self.owner)
        response = self.client.get(self.url)
        self.assertContains(response, 'value="Яєчня"')
        self.assertContains(response, "Посмажити.")
        self.assertContains(response, "Зберегти зміни")

    def test_owner_saves_changes(self):
        self.client.force_login(self.owner)

        response = self.client.post(self.url, self.form_data(meal_type="breakfast"))

        self.assertRedirects(response, reverse("recipes:detail", args=[self.recipe.pk]))
        self.recipe.refresh_from_db()
        self.assertEqual(self.recipe.meal_type, "breakfast")
        self.assertEqual(self.recipe.name, "Яєчня з молоком")
        self.assertEqual(self.recipe.servings, 2)
        self.assertEqual(self.recipe.steps, ["Збити яйця з молоком.", "Посмажити."])
        quantities = {item.product.name: item.quantity for item in self.recipe.ingredients.all()}
        self.assertEqual(quantities, {"Яйце": 3, "Молоко": 50})

    def test_invalid_form_keeps_recipe(self):
        self.client.force_login(self.owner)
        response = self.client.post(self.url, self.form_data(**{"ingredients-0-quantity": ""}))
        self.assertContains(response, "Вкажи кількість")
        self.recipe.refresh_from_db()
        self.assertEqual(self.recipe.name, "Яєчня")
        self.assertEqual(self.recipe.ingredients.count(), 2)

    def test_only_owner_can_edit(self):
        shared = self.make_recipe(name="Спільна")
        self.client.force_login(self.stranger)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.post(self.url, self.form_data()).status_code, 404)
        self.assertEqual(self.client.get(reverse("recipes:edit", args=[shared.pk])).status_code, 404)

class MealTypeTests(RecipeTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.omelette = self.make_recipe(name="Омлет")
        self.omelette.meal_type = Recipe.BREAKFAST
        self.omelette.save()
        self.soup = Recipe.objects.create(name="Суп", instructions="Зварити.", meal_type=Recipe.LUNCH)
        RecipeIngredient.objects.create(recipe=self.soup, product=self.salt, quantity=5)
        FridgeItem.objects.create(user=self.owner, product=self.eggs, quantity=10)
        self.client.force_login(self.owner)

    def names(self, response, key="matches"):
        return [match.recipe.name for match in response.context[key]]

    def test_list_filters_by_meal(self):
        response = self.client.get(reverse("recipes:list"), {"meal": "breakfast"})
        self.assertEqual(self.names(response), ["Омлет"])
        self.assertContains(response, "Сніданок")

    def test_meal_and_status_filters_combine(self):
        response = self.client.get(reverse("recipes:list"), {"meal": "lunch", "filter": "ready"})
        self.assertEqual(self.names(response), [])
        meals = {item["label"]: item["count"] for item in response.context["meals"]}
        self.assertEqual(meals["Сніданок"], 1)
        self.assertEqual(meals["Обід"], 0)
        ready = next(item for item in response.context["filters"] if item["label"] == "Можна приготувати")
        self.assertEqual(ready["url"], "?filter=ready&meal=lunch")

    def test_unknown_meal_shows_all(self):
        response = self.client.get(reverse("recipes:list"), {"meal": "brunch"})
        self.assertEqual(sorted(self.names(response)), ["Омлет", "Суп"])

    def test_what_can_i_cook_filters_by_meal(self):
        response = self.client.get(reverse("recipes:what_can_i_cook"), {"meal": "lunch"})
        self.assertEqual(self.names(response, "ready"), [])
        response = self.client.get(reverse("recipes:what_can_i_cook"), {"meal": "breakfast"})
        self.assertEqual(self.names(response, "ready"), ["Омлет"])

    def test_detail_shows_meal(self):
        response = self.client.get(reverse("recipes:detail", args=[self.soup.pk]))
        self.assertContains(response, "Обід ·")


class ServingsTests(RecipeTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.recipe = self.make_recipe()
        self.client.force_login(self.owner)
        self.url = reverse("recipes:detail", args=[self.recipe.pk])

    def cook(self, **data):
        return self.client.post(reverse("recipes:cook", args=[self.recipe.pk]), data)

    def test_default_servings(self):
        response = self.client.get(self.url)

        self.assertEqual(response.context["servings"], 1)
        self.assertEqual(response.context["match"].required[0].needed, 2)
        self.assertContains(response, "Рецепт на 1 порцію")
        self.assertContains(response, "?servings=2")
        self.assertNotContains(response, "?servings=0")

    def test_scales_ingredients_and_status(self):
        FridgeItem.objects.create(user=self.owner, product=self.eggs, quantity=4)

        response = self.client.get(self.url, {"servings": 3})

        match = response.context["match"]
        self.assertEqual(match.required[0].needed, 6)
        self.assertNotEqual(match.status, READY)
        self.assertContains(response, "бракує 2 шт")
        self.assertContains(response, "Не вистачає продуктів")
        self.assertEqual(match.nutrition["calories"], 143)

    def test_invalid_servings_are_clamped(self):
        for value, expected in [("abc", 1), ("0", 1), ("99", 10)]:
            response = self.client.get(self.url, {"servings": value})
            self.assertEqual(response.context["servings"], expected)

    def test_scales_from_recipe_servings(self):
        self.recipe.servings = 4
        self.recipe.save()
        self.recipe.ingredients.filter(product=self.eggs).update(quantity=8)

        response = self.client.get(self.url, {"servings": 1})

        self.assertEqual(response.context["match"].required[0].needed, 2)
        self.assertContains(response, "Рецепт на 4 порції")

    def test_cook_writes_off_servings_and_logs_eaten(self):
        FridgeItem.objects.create(user=self.owner, product=self.eggs, quantity=7)

        response = self.cook(servings=3, eaten=2)

        log = CookingLog.objects.get()
        self.assertRedirects(response, f"{reverse('recipes:log')}?cooked={log.pk}")
        self.assertEqual(FridgeItem.objects.get(product=self.eggs).quantity, 1)
        self.assertEqual(log.servings, 2)
        self.assertEqual(log.calories_consumed, 286)
        self.assertContains(self.client.get(reverse("recipes:log")), "2 порції")

    def test_eaten_cannot_exceed_cooked(self):
        FridgeItem.objects.create(user=self.owner, product=self.eggs, quantity=4)

        self.cook(servings=2, eaten=5)

        self.assertEqual(CookingLog.objects.get().servings, 2)

    def test_cook_without_enough_products(self):
        FridgeItem.objects.create(user=self.owner, product=self.eggs, quantity=4)

        response = self.cook(servings=3, eaten=1)

        self.assertRedirects(response, f"{self.url}?servings=3")
        self.assertFalse(CookingLog.objects.exists())
        self.assertEqual(FridgeItem.objects.get(product=self.eggs).quantity, 4)

    def test_cook_defaults_to_one_eaten(self):
        FridgeItem.objects.create(user=self.owner, product=self.eggs, quantity=2)

        self.cook()

        log = CookingLog.objects.get()
        self.assertEqual(log.servings, 1)
        self.assertEqual(log.calories_consumed, 143)


class UndoCookTests(RecipeTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.recipe = self.make_recipe()
        self.client.force_login(self.owner)
        self.expiry = date(2030, 1, 1)
        FridgeItem.objects.create(user=self.owner, product=self.eggs, quantity=1, expiry_date=self.expiry)
        FridgeItem.objects.create(user=self.owner, product=self.eggs, quantity=5)

    def cook(self):
        response = self.client.post(reverse("recipes:cook", args=[self.recipe.pk]))
        return CookingLog.objects.get(), response

    def test_cook_remembers_written_off_batches(self):
        log, response = self.cook()

        self.assertEqual(log.written_off, [
            {"product": self.eggs.pk, "quantity": 1, "expiry_date": "2030-01-01"},
            {"product": self.eggs.pk, "quantity": 1, "expiry_date": None},
        ])
        page = self.client.get(response.url)
        self.assertContains(page, "Записано: Яєчня, 1 порція")
        self.assertContains(page, "Яйце 2 шт")
        self.assertContains(page, reverse("recipes:undo_cook", args=[log.pk]))

    def test_undo_returns_products(self):
        log, _ = self.cook()

        response = self.client.post(reverse("recipes:undo_cook", args=[log.pk]), follow=True)

        self.assertRedirects(response, reverse("recipes:log"))
        self.assertContains(response, "Запис скасовано")
        self.assertFalse(CookingLog.objects.exists())
        items = FridgeItem.objects.filter(product=self.eggs)
        self.assertEqual(sum(item.quantity for item in items), 6)
        self.assertTrue(items.filter(quantity=1, expiry_date=self.expiry).exists())

    def test_undo_expires_after_30_minutes(self):
        log, _ = self.cook()
        CookingLog.objects.filter(pk=log.pk).update(cooked_at=timezone.now() - timedelta(minutes=31))

        response = self.client.post(reverse("recipes:undo_cook", args=[log.pk]), follow=True)

        self.assertContains(response, "лише протягом 30 хвилин")
        self.assertTrue(CookingLog.objects.exists())
        self.assertNotContains(response, reverse("recipes:undo_cook", args=[log.pk]))

    def test_old_logs_without_details_cannot_be_undone(self):
        log = CookingLog.objects.create(user=self.owner, recipe=self.recipe, calories_consumed=143)

        self.client.post(reverse("recipes:undo_cook", args=[log.pk]))

        self.assertTrue(CookingLog.objects.filter(pk=log.pk).exists())

    def test_cannot_undo_foreign_log(self):
        log, _ = self.cook()
        self.client.force_login(self.stranger)

        response = self.client.post(reverse("recipes:undo_cook", args=[log.pk]))

        self.assertEqual(response.status_code, 404)
        self.assertTrue(CookingLog.objects.exists())


class DailyCaloriesTests(RecipeTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)
        self.url = reverse("recipes:log")

    def test_default_norm_and_progress(self):
        CookingLog.objects.create(user=self.owner, calories_consumed=1500)

        response = self.client.get(self.url)

        self.assertEqual(response.context["norm"], 2000)
        self.assertEqual(response.context["progress"], 75)
        self.assertContains(response, "залишилось 500 ккал")

    def test_over_norm(self):
        CookingLog.objects.create(user=self.owner, calories_consumed=2300)

        response = self.client.get(self.url)

        self.assertTrue(response.context["over_norm"])
        self.assertEqual(response.context["progress"], 100)
        self.assertContains(response, "понад норму на 300")
        self.assertTrue(response.context["week_days"][-1]["over"])

    def test_change_norm(self):
        response = self.client.post(reverse("recipes:daily_calories"), {"daily_calories": 1800}, follow=True)

        self.assertEqual(Profile.objects.get(user=self.owner).daily_calories, 1800)
        self.assertContains(response, "Денну норму змінено на 1800 ккал")
        self.assertEqual(response.context["norm"], 1800)

    def test_invalid_norm(self):
        response = self.client.post(reverse("recipes:daily_calories"), {"daily_calories": 100}, follow=True)

        self.assertContains(response, "не менше 800")
        self.assertEqual(Profile.for_user(self.owner).daily_calories, 2000)


def png_file(name="photo.png"):
    from io import BytesIO

    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (4, 4), "red").save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


class RecipePhotoTests(RecipeFormMixin, TestCase):
    def setUp(self):
        import tempfile

        from django.test import override_settings

        super().setUp()
        self.media = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(MEDIA_ROOT=self.media.name)
        self.settings_override.enable()
        self.client.force_login(self.owner)

    def tearDown(self):
        self.settings_override.disable()
        self.media.cleanup()

    def test_large_photo_rejected(self):
        from unittest.mock import patch

        with patch("recipes.forms.MAX_PHOTO_SIZE", 10):
            response = self.client.post(self.url, self.form_data(image=png_file()))

        self.assertContains(response, "Фото завелике")
        self.recipe.refresh_from_db()
        self.assertFalse(self.recipe.image)

    def test_replaced_and_deleted_photo_removed_from_disk(self):
        import os

        self.client.post(self.url, self.form_data(image=png_file("first.png")))
        self.recipe.refresh_from_db()
        first = self.recipe.image.path
        self.assertTrue(os.path.exists(first))

        self.client.post(self.url, self.form_data(image=png_file("second.png")))
        self.recipe.refresh_from_db()
        second = self.recipe.image.path
        self.assertFalse(os.path.exists(first))
        self.assertTrue(os.path.exists(second))

        self.client.post(reverse("recipes:delete", args=[self.recipe.pk]))
        self.assertFalse(os.path.exists(second))
