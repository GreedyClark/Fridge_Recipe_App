import re

import pytest
from playwright.sync_api import expect

from accounts.models import Membership
from fridge.models import FridgeItem, ShoppingItem
from recipes.models import Recipe, RecipeIngredient

from conftest import PASSWORD

pytestmark = pytest.mark.django_db(transaction=True)


def choose_product(page, product):
    page.evaluate(
        "(id) => { const s = document.querySelector('#id_product'); s.tomselect ? s.tomselect.setValue(id) : (s.value = id); }",
        str(product.pk),
    )


def test_signup_add_cook_and_undo(page, live_server, products):
    recipe = Recipe.objects.create(name="Омлет", instructions="Збити яйця.\nПосмажити.")
    RecipeIngredient.objects.create(recipe=recipe, product=products["eggs"], quantity=2)

    page.goto(f"{live_server.url}/signup/")
    page.fill("input[name=username]", "newcook")
    page.fill("input[name=password1]", PASSWORD)
    page.fill("input[name=password2]", PASSWORD)
    page.click("form button[type=submit]")
    expect(page).to_have_url(f"{live_server.url}/")

    page.goto(f"{live_server.url}/add/")
    choose_product(page, products["eggs"])
    page.fill("#id_quantity", "3")
    page.click("form button[type=submit]")
    expect(page.locator("body")).to_contain_text("Яйце куряче")

    page.goto(f"{live_server.url}/recipes/what-can-i-cook/")
    page.get_by_role("link", name=re.compile("Омлет")).first.click()
    page.locator("form.cook-form button[type=submit]").first.click()
    expect(page.locator(".undo-banner")).to_contain_text("Записано: Омлет")
    assert FridgeItem.objects.get().quantity == 1

    page.locator(".undo-banner button[type=submit]").click()
    expect(page.locator(".undo-banner")).to_have_count(0)
    assert FridgeItem.objects.get().quantity == 3


def test_import_from_pasted_text(page, live_server, products, make_user, login, monkeypatch):
    cheese, eggs = products["cheese"], products["eggs"]

    def fake_parse(raw, client=None, user=None):
        assert "Сирники" in raw.page_text
        return {
            "name": "Сирники",
            "original_name": "",
            "meal_type": "breakfast",
            "servings": 2,
            "steps": ["Змішати сир з яйцем.", "Обсмажити."],
            "ingredients": [
                {"original": "Сир 500 г", "product_id": cheese.pk, "new_product_name": "", "usda_query": "",
                 "unit": "g", "quantity": 500, "grams_per_piece": None, "optional": False, "warning": ""},
                {"original": "Яйце 1 шт", "product_id": eggs.pk, "new_product_name": "", "usda_query": "",
                 "unit": "pcs", "quantity": 1, "grams_per_piece": None, "optional": False, "warning": ""},
            ],
            "image_url": "",
            "source_url": "",
        }

    monkeypatch.setattr("recipes.importing.pipeline.parse_recipe", fake_parse)
    login(page, make_user("cook"))

    page.goto(f"{live_server.url}/recipes/import/")
    page.get_by_role("tab", name="Текст рецепта").click()
    page.fill("textarea[name=text]", "Сирники\n\nСир кисломолочний 500 г\nЯйце 1 шт\n\nЗмішати й обсмажити до золотистого кольору.")
    page.get_by_role("button", name="Розібрати текст").click()

    expect(page.locator("body")).to_contain_text("Імпорт · з тексту")
    page.click("#save-recipe")
    expect(page.locator("h1")).to_contain_text("Сирники")
    recipe = Recipe.objects.get(name="Сирники")
    assert recipe.source_url == ""
    assert recipe.ingredients.count() == 2


def test_buy_everything(page, live_server, products, make_user, login):
    user = make_user("cook")
    ShoppingItem.objects.create(user=user, product=products["milk"], quantity=1200)
    ShoppingItem.objects.create(user=user, product=products["eggs"], quantity=10)
    login(page, user)

    page.goto(f"{live_server.url}/shopping/")
    expect(page.locator(".shop-qty").first).to_have_text("1,2 л")
    page.get_by_role("button", name="Купив усе (2)").click()
    page.get_by_role("button", name="Так, у холодильник").click()

    expect(page.locator(".shop-status")).to_contain_text("У холодильник перенесено: 2 продукти.")
    expect(page).to_have_url(f"{live_server.url}/shopping/")
    assert FridgeItem.objects.filter(user=user).count() == 2
    assert not ShoppingItem.objects.filter(bought=False).exists()


def test_family_shares_fridge_and_shopping(page, browser, live_server, products, make_user, login):
    owner, olia = make_user("peangqin"), make_user("olia")
    login(page, owner)
    page.goto(f"{live_server.url}/profile/")
    page.fill("#id_name", "Дім на Липовій")
    page.get_by_role("button", name="Створити").click()
    code = page.locator("#invite-code").inner_text().strip()

    olia_context = browser.new_context()
    olia_page = olia_context.new_page()
    login(olia_page, olia)
    olia_page.goto(f"{live_server.url}/family/join/{code}/")
    olia_page.get_by_role("button", name="Приєднатися").click()
    expect(olia_page).to_have_url(f"{live_server.url}/")
    assert Membership.objects.filter(household__name="Дім на Липовій").count() == 2

    FridgeItem.objects.create(user=olia, product=products["milk"], quantity=1000)
    ShoppingItem.objects.create(user=olia, product=products["eggs"], quantity=10, source="вручну")

    page.goto(f"{live_server.url}/")
    expect(page.locator("body")).to_contain_text("Молоко")

    page.goto(f"{live_server.url}/shopping/")
    expect(page.locator(".shop-name").first).to_contain_text("olia · вручну")
    page.locator(".take-btn").first.click()
    expect(page.locator(".take-btn").first).to_have_class(re.compile("mine"))

    olia_page.goto(f"{live_server.url}/shopping/")
    hand = olia_page.locator(".take-btn").first
    expect(hand).to_have_class(re.compile("other"))
    expect(hand).to_have_attribute("title", re.compile("Бере peangqin"))
    olia_context.close()
