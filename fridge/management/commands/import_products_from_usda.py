import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from fridge.models import Product

SEARCH_URL = "https://api.nal.usda.gov/fdc/v1/foods/search"
FOOD_URL = "https://api.nal.usda.gov/fdc/v1/food/{}"
TIMEOUT = 20
SEARCH_UNSAFE_CHARS = '"/()'

ENERGY_NUMBERS = ("208", "957", "958")
PROTEIN_NUMBERS = ("203",)
FAT_NUMBERS = ("204",)
CARBS_NUMBERS = ("205", "205.2")

PRODUCTS_TO_IMPORT = {
    "Куряче філе": {"query": "Chicken, broiler or fryers, breast, skinless, boneless, meat only, raw", "unit": "g"},
    "Куряче стегно": {"query": "Chicken, broilers or fryers, thigh, meat only, raw", "unit": "g"},
    "Яловичина": {"query": "Beef, chuck, arm pot roast, separable lean and fat, trimmed to 0\" fat, all grades, raw", "unit": "g"},
    "Свинина": {"query": "Pork, fresh, loin, whole, separable lean and fat, raw", "unit": "g"},
    "Фарш змішаний": {"query": "Beef, ground, 80% lean meat / 20% fat, raw", "unit": "g"},
    "Яйце куряче": {"query": "Egg, whole, raw, fresh", "unit": "pcs", "grams_per_piece": 50},
    "Молоко 2.5%": {"query": "Milk, reduced fat, fluid, 2% milkfat, with added vitamin A and vitamin D", "unit": "ml"},
    "Кефір": {"query": "Kefir, lowfat, plain", "unit": "ml"},
    "Сир кисломолочний": {"query": "Cheese, cottage, lowfat, 2% milkfat", "unit": "g"},
    "Сир твердий": {"query": "Cheese, cheddar", "unit": "g"},
    "Вершкове масло": {"query": "Butter, salted", "unit": "g"},
    "Сметана": {"query": "Cream, sour, cultured", "unit": "g"},
    "Рис": {"query": "Rice, white, long-grain, regular, raw, unenriched", "unit": "g"},
    "Гречка": {"query": "Buckwheat groats, roasted, dry", "unit": "g"},
    "Макарони": {"query": "Pasta, dry, unenriched", "unit": "g"},
    "Вівсянка": {"query": "Cereals, oats, regular and quick, not fortified, dry", "unit": "g"},
    "Пшоно": {"query": "Millet, raw", "unit": "g"},
    "Булгур": {"query": "Bulgur, dry", "unit": "g"},
    "Картопля": {"query": "Potatoes, flesh and skin, raw", "unit": "g"},
    "Морква": {"query": "Carrots, raw", "unit": "g"},
    "Цибуля": {"query": "Onions, raw", "unit": "g"},
    "Часник": {"query": "Garlic, raw", "unit": "g"},
    "Капуста": {"query": "Cabbage, raw", "unit": "g"},
    "Помідор": {"query": "Tomatoes, red, ripe, raw, year round average", "unit": "g"},
    "Огірок": {"query": "Cucumber, with peel, raw", "unit": "g"},
    "Перець солодкий": {"query": "Peppers, sweet, red, raw", "unit": "g"},
    "Буряк": {"query": "Beets, raw", "unit": "g"},
    "Гарбуз": {"query": "Pumpkin, raw", "unit": "g"},
    "Яблуко": {"query": "Apples, raw, with skin", "unit": "pcs", "grams_per_piece": 180},
    "Банан": {"query": "Bananas, raw", "unit": "pcs", "grams_per_piece": 120},
    "Лимон": {"query": "Lemons, raw, without peel", "unit": "pcs", "grams_per_piece": 100},
    "Хліб пшеничний": {"query": "Bread, white, commercially prepared (includes soft bread crumbs)", "unit": "g", "fdc_id": 174924},
    "Хліб житній": {"query": "Bread, rye", "unit": "g"},
    "Олія соняшникова": {"query": "Oil, sunflower, linoleic, (approx. 65%)", "unit": "ml", "fdc_id": 171025},
    "Цукор": {"query": "Sugars, granulated", "unit": "g"},
    "Квасоля суха": {"query": "Beans, kidney, all types, mature seeds, raw", "unit": "g"},
    "Сочевиця суха": {"query": "Lentils, raw", "unit": "g"},
    "Горох сухий": {"query": "Peas, split, mature seeds, raw", "unit": "g"},
    "Лосось": {"query": "Fish, salmon, Atlantic, farmed, raw", "unit": "g"},
    "Минтай": {"query": "Fish, pollock, Alaska, raw", "unit": "g"},
    "Тунець консервований": {"query": "Fish, tuna, light, canned in water, drained solids", "unit": "g"},
    "Ковбаса варена": {"query": "Bologna, beef and pork", "unit": "g"},
    "Сосиски": {"query": "Frankfurter, beef and pork", "unit": "g"},
    "Бекон": {"query": "Pork, cured, bacon, unprepared", "unit": "g"},
}


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


class Command(BaseCommand):
    help = "Імпортує калорійність і БЖУ продуктів з USDA FoodData Central"

    def handle(self, *args, **options):
        if not settings.USDA_API_KEY:
            raise CommandError("USDA_API_KEY не задано. Додайте ключ у файл .env.")

        session = requests.Session()
        session.headers["X-Api-Key"] = settings.USDA_API_KEY
        created = updated = skipped = 0

        for name, params in PRODUCTS_TO_IMPORT.items():
            try:
                food = self.fetch_food(session, params)
            except requests.RequestException as exc:
                self.stdout.write(self.style.WARNING(f"⚠️ {name}: помилка запиту ({exc})"))
                skipped += 1
                continue

            if food is None:
                self.stdout.write(self.style.WARNING(f'⚠️ {name}: нічого не знайдено за запитом "{params["query"]}"'))
                skipped += 1
                continue

            nutrients = extract_nutrients(food)
            calories = pick(nutrients, ENERGY_NUMBERS, unit="KCAL")
            if calories is None:
                self.stdout.write(self.style.WARNING(f'⚠️ {name}: у записі "{food.get("description")}" немає енергії в ккал'))
                skipped += 1
                continue

            _, is_new = Product.objects.update_or_create(
                name=name,
                defaults={
                    "default_unit": params["unit"],
                    "calories_per_100": calories,
                    "protein_per_100": pick(nutrients, PROTEIN_NUMBERS),
                    "fat_per_100": pick(nutrients, FAT_NUMBERS),
                    "carbs_per_100": pick(nutrients, CARBS_NUMBERS),
                    "grams_per_piece": params.get("grams_per_piece"),
                    "usda_fdc_id": food.get("fdcId"),
                },
            )
            if is_new:
                created += 1
            else:
                updated += 1

            self.stdout.write(self.style.SUCCESS(
                f'✅ {name} → {calories:g} ккал/100г (FDC ID: {food.get("fdcId")}, матч: "{food.get("description")}")'
            ))

        self.stdout.write(f"\nСтворено: {created}, оновлено: {updated}, пропущено: {skipped}")

    def fetch_food(self, session, params):
        if params.get("fdc_id"):
            response = session.get(FOOD_URL.format(params["fdc_id"]), timeout=TIMEOUT)
            response.raise_for_status()
            return response.json()

        response = session.get(
            SEARCH_URL,
            params={"query": clean_query(params["query"]), "dataType": "Foundation,SR Legacy", "pageSize": 5},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        foods = response.json().get("foods", [])
        if not foods:
            return None
        return choose_food(foods, params["query"])