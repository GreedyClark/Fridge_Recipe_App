import os

import pytest
from django.contrib.auth.models import User

from fridge.models import Product

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

PASSWORD = "Sup3r-secret-pass"


@pytest.fixture(autouse=True)
def plain_http(settings):
    settings.SESSION_COOKIE_SECURE = False
    settings.CSRF_COOKIE_SECURE = False
    settings.SECURE_SSL_REDIRECT = False


@pytest.fixture
def products(transactional_db):
    return {
        "eggs": Product.objects.create(name="Яйце куряче", default_unit="pcs", calories_per_100=143, grams_per_piece=50),
        "milk": Product.objects.create(name="Молоко", default_unit="ml", calories_per_100=52),
        "cheese": Product.objects.create(name="Сир кисломолочний", default_unit="g", calories_per_100=159),
    }


@pytest.fixture
def make_user(transactional_db):
    def create(username):
        return User.objects.create_user(username, password=PASSWORD)
    return create


@pytest.fixture
def login(live_server):
    def sign_in(page, user):
        page.goto(f"{live_server.url}/login/")
        page.fill("input[name=username]", user.username)
        page.fill("input[name=password]", PASSWORD)
        page.click("form button[type=submit]")
        page.wait_for_url(f"{live_server.url}/")
    return sign_in
