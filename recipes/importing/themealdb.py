import re
import string

import requests
from django.conf import settings

from .errors import ImportFailed
from .extract import RawRecipe, clean

API_URL = "https://www.themealdb.com/api/json/v1/{key}/{endpoint}"
MEAL_URL = "https://www.themealdb.com/meal/{}"
PREFIX = "themealdb:"
TIMEOUT = 15
MAX_INGREDIENTS = 20
STEP_LABEL_RE = re.compile(r"^(step\s*)?\d+[.):]?$", re.IGNORECASE)


def external_id(meal_id):
    return f"{PREFIX}{meal_id}"


def get_meals(session, endpoint, **params):
    url = API_URL.format(key=settings.THEMEALDB_API_KEY, endpoint=endpoint)
    try:
        response = session.get(url, params=params, timeout=TIMEOUT)
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise ImportFailed("TheMealDB не відповідає. Спробуй пізніше.") from exc
    return data.get("meals") or []


def lookup_meal(session, meal_id):
    meals = get_meals(session, "lookup.php", i=meal_id)
    return meals[0] if meals else None


def iter_meals(session, category=None):
    if category:
        for short in get_meals(session, "filter.php", c=category):
            meal = lookup_meal(session, short["idMeal"])
            if meal:
                yield meal
        return
    for letter in string.ascii_lowercase:
        yield from get_meals(session, "search.php", f=letter)


def ingredients_from(meal):
    items = []
    for index in range(1, MAX_INGREDIENTS + 1):
        name = clean(meal.get(f"strIngredient{index}"))
        if not name:
            continue
        measure = clean(meal.get(f"strMeasure{index}"))
        items.append(f"{measure} {name}".strip())
    return items


def meal_to_raw(meal):
    steps = [clean(line) for line in (meal.get("strInstructions") or "").splitlines()]
    return RawRecipe(
        url=MEAL_URL.format(meal["idMeal"]),
        source="themealdb",
        title=clean(meal.get("strMeal")),
        image_url=meal.get("strMealThumb") or "",
        ingredients=ingredients_from(meal),
        instructions=[step for step in steps if step and not STEP_LABEL_RE.match(step)],
        category=clean(meal.get("strCategory")),
    )
