from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from fridge.models import FridgeItem, Product, ShoppingItem
from recipes.models import CookingLog, Recipe, RecipeIngredient

from .family import create_household, join_household, leave_household
from .models import MAX_MEMBERS, Household, Membership, people_ids


class FamilyTestMixin:
    def setUp(self):
        self.me = User.objects.create_user("peangqin", password="pass")
        self.olia = User.objects.create_user("olia", password="pass")
        self.stranger = User.objects.create_user("stranger", password="pass")
        self.eggs = Product.objects.create(name="Яйце", default_unit="pcs", calories_per_100=143, grams_per_piece=50)
        self.client.force_login(self.me)

    def make_family(self):
        household = create_household(self.me, "Дім на Липовій")
        join_household(self.olia, household)
        return household


class FamilyModelTests(FamilyTestMixin, TestCase):
    def test_people_without_family(self):
        self.assertEqual(people_ids(self.me), [self.me.pk])

    def test_people_in_family(self):
        self.make_family()
        self.assertEqual(sorted(people_ids(self.olia)), sorted([self.me.pk, self.olia.pk]))
        self.assertEqual(people_ids(self.stranger), [self.stranger.pk])

    def test_invite_code_format(self):
        household = Household.objects.create(name="Дім")
        self.assertEqual(len(household.invite_code), 8)
        self.assertTrue(household.invite_code.isalnum())
        old = household.invite_code
        household.refresh_code()
        self.assertNotEqual(household.invite_code, old)

    def test_member_limit(self):
        household = self.make_family()
        for index in range(MAX_MEMBERS - 2):
            join_household(User.objects.create_user(f"member{index}"), household)
        with self.assertRaisesMessage(Exception, "більше додати не можна"):
            join_household(self.stranger, household)

    def test_duplicate_private_products_merge_on_join(self):
        household = create_household(self.me, "Дім")
        mine = Product.objects.create(owner=self.me, name="Авокадо", default_unit="g", calories_per_100=160)
        hers = Product.objects.create(owner=self.olia, name="авокадо", default_unit="g", calories_per_100=160)
        FridgeItem.objects.create(user=self.olia, product=hers, quantity=200)
        ShoppingItem.objects.create(user=self.me, product=mine, quantity=100)
        ShoppingItem.objects.create(user=self.olia, product=hers, quantity=50)
        recipe = Recipe.objects.create(owner=self.olia, name="Тост", instructions="-")
        RecipeIngredient.objects.create(recipe=recipe, product=hers, quantity=100)

        join_household(self.olia, household)

        self.assertFalse(Product.objects.filter(pk=hers.pk).exists())
        self.assertEqual(FridgeItem.objects.get().product, mine)
        self.assertEqual(recipe.ingredients.get().product, mine)
        item = ShoppingItem.objects.get()
        self.assertEqual(item.product, mine)
        self.assertEqual(item.quantity, 150)

    def test_leaving_takes_own_things_and_keeps_shared_usage(self):
        self.make_family()
        mine = Product.objects.create(owner=self.me, name="Тофу", default_unit="g", calories_per_100=76)
        FridgeItem.objects.create(user=self.olia, product=mine, quantity=300)
        my_recipe = Recipe.objects.create(owner=self.me, name="Мій суп", instructions="-")
        her_item = ShoppingItem.objects.create(user=self.olia, product=self.eggs, quantity=2, taken_by=self.me)

        leave_household(self.me)

        self.assertFalse(Membership.objects.filter(user=self.me).exists())
        self.assertEqual(Membership.objects.get(user=self.olia).role, Membership.OWNER)
        self.assertNotIn(my_recipe, Recipe.objects.visible_to(self.olia))
        her_fridge = FridgeItem.objects.get(user=self.olia)
        self.assertEqual(her_fridge.product.owner, self.olia)
        self.assertEqual(her_fridge.product.name, "Тофу")
        self.assertTrue(Product.objects.filter(pk=mine.pk, owner=self.me).exists())
        her_item.refresh_from_db()
        self.assertIsNone(her_item.taken_by)

    def test_last_member_leaving_deletes_family(self):
        create_household(self.me, "Дім")
        leave_household(self.me)
        self.assertFalse(Household.objects.exists())


class SharedDataTests(FamilyTestMixin, TestCase):
    def test_family_sees_one_fridge(self):
        self.make_family()
        FridgeItem.objects.create(user=self.olia, product=self.eggs, quantity=4)
        response = self.client.get(reverse("fridge:list"))
        self.assertContains(response, "Яйце")
        self.client.force_login(self.stranger)
        self.assertNotContains(self.client.get(reverse("fridge:list")), "Яйце")

    def test_adding_merges_with_family_row(self):
        self.make_family()
        FridgeItem.objects.create(user=self.olia, product=self.eggs, quantity=4)
        FridgeItem.add(self.me, self.eggs, 6)
        self.assertEqual(FridgeItem.objects.get().quantity, 10)

    def test_family_can_edit_each_others_fridge_rows(self):
        self.make_family()
        item = FridgeItem.objects.create(user=self.olia, product=self.eggs, quantity=4)
        self.client.post(reverse("fridge:delete", args=[item.pk]))
        self.assertFalse(FridgeItem.objects.exists())

    def test_cooking_uses_family_fridge_and_log_is_personal(self):
        self.make_family()
        recipe = Recipe.objects.create(owner=self.olia, name="Омлет", instructions="-")
        RecipeIngredient.objects.create(recipe=recipe, product=self.eggs, quantity=2)
        FridgeItem.objects.create(user=self.olia, product=self.eggs, quantity=3)

        self.client.post(reverse("recipes:cook", args=[recipe.pk]))

        self.assertEqual(FridgeItem.objects.get().quantity, 1)
        self.assertEqual(CookingLog.objects.get().user, self.me)
        self.client.force_login(self.olia)
        self.assertNotContains(self.client.get(reverse("recipes:log")), "Омлет")

    def test_family_recipes_visible_and_editable(self):
        self.make_family()
        recipe = Recipe.objects.create(owner=self.olia, name="Сирники", instructions="-")
        detail = self.client.get(reverse("recipes:detail", args=[recipe.pk]))
        self.assertContains(detail, "Рецепт сім'ї · olia")
        self.assertEqual(self.client.get(reverse("recipes:edit", args=[recipe.pk])).status_code, 200)
        self.client.force_login(self.stranger)
        self.assertEqual(self.client.get(reverse("recipes:detail", args=[recipe.pk])).status_code, 404)

    def test_family_products_in_catalog(self):
        self.make_family()
        Product.objects.create(owner=self.olia, name="Хумус", default_unit="g", calories_per_100=166)
        self.assertTrue(Product.objects.visible_to(self.me).filter(name="Хумус").exists())
        self.assertFalse(Product.objects.visible_to(self.stranger).filter(name="Хумус").exists())


class FamilyShoppingTests(FamilyTestMixin, TestCase):
    htmx = {"HTTP_HX_REQUEST": "true"}

    def setUp(self):
        super().setUp()
        self.make_family()
        self.item = ShoppingItem.objects.create(user=self.olia, product=self.eggs, quantity=10, source="вручну")

    def test_shared_list_and_badge(self):
        response = self.client.get(reverse("fridge:shopping"))
        self.assertContains(response, "olia · вручну")
        self.assertEqual(response.context["shopping_count"], 1)
        self.assertContains(response, "Купити (Дім на Липовій):")

    def test_take_toggles_hand(self):
        url = reverse("fridge:shopping_take", args=[self.item.pk])
        response = self.client.post(url, **self.htmx)
        self.item.refresh_from_db()
        self.assertEqual(self.item.taken_by, self.me)
        self.assertContains(response, 'class="take-btn mine"')

        self.client.force_login(self.olia)
        response = self.client.get(reverse("fridge:shopping"))
        self.assertContains(response, 'class="take-btn other"')
        self.assertContains(response, "Бере peangqin")

        self.client.force_login(self.me)
        self.client.post(url)
        self.item.refresh_from_db()
        self.assertIsNone(self.item.taken_by)

    def test_stranger_cannot_take(self):
        self.client.force_login(self.stranger)
        response = self.client.post(reverse("fridge:shopping_take", args=[self.item.pk]))
        self.assertEqual(response.status_code, 404)

    def test_buy_only_mine(self):
        milk = Product.objects.create(name="Молоко", default_unit="ml", calories_per_100=52)
        mine = ShoppingItem.objects.create(user=self.olia, product=milk, quantity=1000, taken_by=self.me)
        response = self.client.post(reverse("fridge:shopping_buy_all"), {"only": "mine"}, **self.htmx)
        mine.refresh_from_db()
        self.item.refresh_from_db()
        self.assertTrue(mine.bought)
        self.assertEqual(mine.bought_by, self.me)
        self.assertFalse(self.item.bought)
        self.assertContains(response, "У холодильник перенесено: 1 продукт.")

    def test_bought_shows_who(self):
        self.client.post(reverse("fridge:shopping_buy", args=[self.item.pk]))
        self.client.force_login(self.olia)
        response = self.client.get(reverse("fridge:shopping"))
        self.assertContains(response, '<i class="ti ti-user"></i>peangqin', html=True)


class FamilyViewTests(FamilyTestMixin, TestCase):
    def test_profile_without_family(self):
        response = self.client.get(reverse("profile"))
        self.assertContains(response, "Створити сім'ю")
        self.assertContains(response, "Приєднатися")

    def test_create_family(self):
        response = self.client.post(reverse("family_create"), {"name": "Дім на Липовій"}, follow=True)
        self.assertContains(response, "«Дім на Липовій» створено.")
        household = Household.objects.get()
        self.assertContains(response, household.invite_code)
        self.assertContains(response, f"/family/join/{household.invite_code}/")
        self.assertTrue(Membership.objects.get(user=self.me).is_owner)

    def test_join_by_code(self):
        household = create_household(self.olia, "Дім")
        self.client.post(reverse("family_join"), {"code": f" {household.invite_code.lower()} "})
        self.assertEqual(Membership.objects.get(user=self.me).household, household)

    def test_join_with_wrong_code(self):
        response = self.client.post(reverse("family_join"), {"code": "NOPE1234"})
        self.assertContains(response, "Такого коду немає")
        self.assertFalse(Membership.objects.exists())

    def test_join_by_link(self):
        household = create_household(self.olia, "Дім")
        url = reverse("family_invite", args=[household.invite_code])
        self.assertContains(self.client.get(url), "Приєднатися")
        response = self.client.post(url)
        self.assertRedirects(response, reverse("fridge:list"))
        self.assertEqual(Membership.objects.get(user=self.me).household, household)

    def test_link_requires_login(self):
        household = create_household(self.olia, "Дім")
        self.client.logout()
        url = reverse("family_invite", args=[household.invite_code])
        self.assertRedirects(self.client.get(url), f"{reverse('login')}?next={url}")

    def test_cannot_join_second_family(self):
        create_household(self.me, "Мій дім")
        other = create_household(self.olia, "Інший")
        response = self.client.post(reverse("family_invite", args=[other.invite_code]), follow=True)
        self.assertContains(response, "Ти вже в сім&#x27;ї")
        self.assertEqual(Membership.objects.get(user=self.me).household.name, "Мій дім")

    def test_old_code_stops_working(self):
        household = self.make_family()
        old = household.invite_code
        self.client.post(reverse("family_code"))
        household.refresh_from_db()
        self.assertNotEqual(household.invite_code, old)
        self.assertEqual(self.client.get(reverse("family_invite", args=[old])).status_code, 404)

    def test_owner_removes_member(self):
        self.make_family()
        self.client.post(reverse("family_remove", args=[self.olia.pk]))
        self.assertFalse(Membership.objects.filter(user=self.olia).exists())

    def test_member_cannot_remove_or_refresh(self):
        household = self.make_family()
        self.client.force_login(self.olia)
        self.assertEqual(self.client.post(reverse("family_remove", args=[self.me.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("family_code")).status_code, 404)
        self.assertEqual(Household.objects.get().invite_code, household.invite_code)

    def test_leave(self):
        self.make_family()
        self.client.force_login(self.olia)
        response = self.client.post(reverse("family_leave"), follow=True)
        self.assertContains(response, "Ти більше не в сім&#x27;ї «Дім на Липовій».")
        self.assertEqual(people_ids(self.olia), [self.olia.pk])
