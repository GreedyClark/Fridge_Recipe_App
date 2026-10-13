from datetime import date

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from recipes.models import Recipe, RecipeIngredient

from .models import FridgeItem, Product, ShoppingItem


class ShoppingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("cook", password="pass")
        self.other = User.objects.create_user("other", password="pass")
        self.eggs = Product.objects.create(name="Яйце", default_unit="pcs", calories_per_100=143, grams_per_piece=50)
        self.milk = Product.objects.create(name="Молоко", default_unit="ml", calories_per_100=52)
        self.client.force_login(self.user)

    def add(self, product, quantity, **extra):
        data = {"product": product.pk, "quantity": quantity, **extra}
        return self.client.post(reverse("fridge:shopping_add"), data)

    def test_add_and_merge(self):
        self.add(self.milk, 500)
        response = self.add(self.milk, 250)

        self.assertRedirects(response, reverse("fridge:shopping"))
        item = ShoppingItem.objects.get()
        self.assertEqual(item.quantity, 750)
        self.assertEqual(item.source, "вручну")

    def test_page_lists_items_and_badge(self):
        self.add(self.milk, 500)
        ShoppingItem.objects.create(user=self.other, product=self.eggs, quantity=10)

        response = self.client.get(reverse("fridge:shopping"))

        self.assertContains(response, "Молоко")
        self.assertNotContains(response, "<span class=\"fw-bold\">Яйце</span>", html=False)
        self.assertEqual(response.context["shopping_count"], 1)
        self.assertContains(response, 'class="nav-badge"')

    def test_buy_moves_to_fridge(self):
        self.add(self.eggs, 6)
        item = ShoppingItem.objects.get()

        response = self.client.post(reverse("fridge:shopping_buy", args=[item.pk]), {"expiry_date": "2030-05-01"})

        self.assertRedirects(response, reverse("fridge:shopping"))
        fridge = FridgeItem.objects.get(user=self.user)
        self.assertEqual((fridge.product, fridge.quantity, fridge.expiry_date), (self.eggs, 6, date(2030, 5, 1)))
        item.refresh_from_db()
        self.assertTrue(item.bought)
        page = self.client.get(reverse("fridge:shopping"))
        self.assertContains(page, "Куплено")
        self.assertEqual(page.context["shopping_count"], 0)

    def test_buy_without_expiry_and_twice(self):
        self.add(self.eggs, 6)
        item = ShoppingItem.objects.get()
        url = reverse("fridge:shopping_buy", args=[item.pk])

        self.client.post(url)
        second = self.client.post(url)

        self.assertIsNone(FridgeItem.objects.get().expiry_date)
        self.assertEqual(second.status_code, 404)

    def test_bought_item_does_not_merge(self):
        self.add(self.eggs, 6)
        ShoppingItem.objects.update(bought=True)

        self.add(self.eggs, 2)

        self.assertEqual(ShoppingItem.objects.filter(bought=False).get().quantity, 2)

    def test_delete_and_clear(self):
        self.add(self.eggs, 6)
        self.add(self.milk, 500)
        eggs, milk = ShoppingItem.objects.order_by("pk")
        milk.bought = True
        milk.save()

        self.client.post(reverse("fridge:shopping_clear"))
        self.assertEqual(list(ShoppingItem.objects.all()), [eggs])

        self.client.post(reverse("fridge:shopping_delete", args=[eggs.pk]))
        self.assertFalse(ShoppingItem.objects.exists())

    def test_foreign_items_protected(self):
        item = ShoppingItem.objects.create(user=self.other, product=self.eggs, quantity=1)

        self.assertEqual(self.client.post(reverse("fridge:shopping_buy", args=[item.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("fridge:shopping_delete", args=[item.pk])).status_code, 404)

    def test_private_product_of_other_user_rejected(self):
        secret = Product.objects.create(name="Чужий соус", default_unit="g", calories_per_100=10, owner=self.other)

        self.add(secret, 100)

        self.assertFalse(ShoppingItem.objects.exists())

    def test_safe_next_redirect(self):
        response = self.add(self.milk, 100, next="https://evil.example.com/")
        self.assertRedirects(response, reverse("fridge:shopping"))

        response = self.add(self.milk, 100, next="/recipes/what-can-i-cook/")
        self.assertRedirects(response, "/recipes/what-can-i-cook/")

    def test_what_can_i_cook_chip(self):
        recipe = Recipe.objects.create(name="Омлет", instructions="Посмажити.")
        RecipeIngredient.objects.create(recipe=recipe, product=self.eggs, quantity=3)
        url = reverse("recipes:what_can_i_cook")

        page = self.client.get(url)
        self.assertContains(page, "у список")
        self.assertContains(page, 'name="quantity" value="3"')

        self.add(self.eggs, 3, source="з «Чого бракує?»", next=url)
        page = self.client.get(url)
        self.assertContains(page, "у списку")
        self.assertEqual(ShoppingItem.objects.get().source, "з «Чого бракує?»")

    def test_add_missing_from_recipe(self):
        recipe = Recipe.objects.create(name="Омлет", instructions="Посмажити.")
        RecipeIngredient.objects.create(recipe=recipe, product=self.eggs, quantity=2)
        RecipeIngredient.objects.create(recipe=recipe, product=self.milk, quantity=100)
        FridgeItem.objects.create(user=self.user, product=self.eggs, quantity=3)
        FridgeItem.objects.create(user=self.user, product=self.milk, quantity=500)

        response = self.client.post(reverse("recipes:add_missing", args=[recipe.pk]), {"servings": 3}, follow=True)

        self.assertRedirects(response, f"{reverse('recipes:detail', args=[recipe.pk])}?servings=3")
        self.assertContains(response, "Додано в список покупок: Яйце")
        item = ShoppingItem.objects.get()
        self.assertEqual((item.product, item.quantity, item.source), (self.eggs, 3, "для: Омлет"))


class ErrorPageTests(TestCase):
    def test_custom_404(self):
        user = User.objects.create_user("cook", password="pass")
        self.client.force_login(user)

        response = self.client.get("/no-such-page/")

        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Сторінку не знайдено", status_code=404)
