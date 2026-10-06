from django.core.management.base import BaseCommand, CommandError

from fridge.usda import USDAError, import_product, make_session

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




class Command(BaseCommand):
    help = "Імпортує калорійність і БЖУ продуктів з USDA FoodData Central"

    def handle(self, *args, **options):
        try:
            session = make_session()
        except USDAError as exc:
            raise CommandError(str(exc)) from exc

        created = updated = skipped = 0
        for name, params in PRODUCTS_TO_IMPORT.items():
            try:
                product, is_new, food = import_product(
                    name,
                    params["query"],
                    params["unit"],
                    grams_per_piece=params.get("grams_per_piece"),
                    fdc_id=params.get("fdc_id"),
                    session=session,
                )
            except USDAError as exc:
                self.stdout.write(self.style.WARNING(f"⚠️ {name}: {exc}"))
                skipped += 1
                continue

            if is_new:
                created += 1
            else:
                updated += 1
            self.stdout.write(self.style.SUCCESS(
                f'✅ {name} → {product.calories_per_100:g} ккал/100г '
                f'(FDC ID: {food.get("fdcId")}, матч: "{food.get("description")}")'
            ))

        self.stdout.write(f"\nСтворено: {created}, оновлено: {updated}, пропущено: {skipped}")