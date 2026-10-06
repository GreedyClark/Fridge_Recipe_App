import requests
from django.conf import settings

from .models import Product

SEARCH_URL = "https://api.nal.usda.gov/fdc/v1/foods/search"
FOOD_URL = "https://api.nal.usda.gov/fdc/v1/food/{}"
TIMEOUT = 20
SEARCH_UNSAFE_CHARS = '"/()'

ENERGY_NUMBERS = ("208", "957", "958")
PROTEIN_NUMBERS = ("203",)
FAT_NUMBERS = ("204",)
CARBS_NUMBERS = ("205", "205.2")


class USDAError(Exception):
    pass


def make_session():
    if not settings.USDA_API_KEY:
        raise USDAError("USDA_API_KEY не задано. Додайте ключ у файл .env.")
    session = requests.Session()
    session.headers["X-Api-Key"] = settings.USDA_API_KEY
    return session


def extract_nutrients(food):
    nutrients = {}
    for item in food.get("foodNutrients", []):
        nested = item.get("nutrient") or {}
        number = str(item.get("nutrientNumber") or nested.get("number") or "")
        unit = (item.get("unitName") or nested.get("unitName") or "").upper()
        value = item.get("value", item.get("amount"))
        if number and value is not None and number not in nutrients:
            nutrients[number] = (value, unit)
    return nutrients


def pick(nutrients, numbers, unit=None):
    for number in numbers:
        if number in nutrients:
            value, value_unit = nutrients[number]
            if unit is None or value_unit == unit:
                return round(value, 1)
    return None


def clean_query(query):
    for char in SEARCH_UNSAFE_CHARS:
        query = query.replace(char, " ")
    return " ".join(query.split())


def choose_food(foods, query):
    query = query.lower()
    exact = [food for food in foods if food.get("description", "").lower() == query]
    if exact:
        return exact[0]
    if "raw" in query:
        raw = [food for food in foods if "raw" in food.get("description", "").lower()]
        if raw:
            return raw[0]
    return foods[0]


def fetch_food(session, query, fdc_id=None):
    if fdc_id:
        response = session.get(FOOD_URL.format(fdc_id), timeout=TIMEOUT)
        response.raise_for_status()
        return response.json()

    response = session.get(
        SEARCH_URL,
        params={"query": clean_query(query), "dataType": "Foundation,SR Legacy", "pageSize": 5},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    foods = response.json().get("foods", [])
    if not foods:
        return None
    return choose_food(foods, query)


def import_product(name, query, unit, grams_per_piece=None, fdc_id=None, session=None):
    session = session or make_session()
    try:
        food = fetch_food(session, query, fdc_id)
    except requests.RequestException as exc:
        raise USDAError(f"помилка запиту ({exc})") from exc

    if food is None:
        raise USDAError(f'нічого не знайдено за запитом "{query}"')

    nutrients = extract_nutrients(food)
    calories = pick(nutrients, ENERGY_NUMBERS, unit="KCAL")
    if calories is None:
        raise USDAError(f'у записі "{food.get("description")}" немає енергії в ккал')

    product, created = Product.objects.update_or_create(
        name=name,
        defaults={
            "default_unit": unit,
            "calories_per_100": calories,
            "protein_per_100": pick(nutrients, PROTEIN_NUMBERS),
            "fat_per_100": pick(nutrients, FAT_NUMBERS),
            "carbs_per_100": pick(nutrients, CARBS_NUMBERS),
            "grams_per_piece": grams_per_piece,
            "usda_fdc_id": food.get("fdcId"),
        },
    )
    return product, created, food