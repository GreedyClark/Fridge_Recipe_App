import time

import requests
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from recipes.importing.errors import ImportFailed, QuotaExceeded
from recipes.importing.gemini import parse_recipe
from recipes.importing.themealdb import external_id, iter_meals, meal_to_raw
from recipes.models import RecipeImport


class Command(BaseCommand):
    help = "Імпортує страви з TheMealDB як чернетки для спільної бази рецептів"

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=10, help="скільки нових страв імпортувати")
        parser.add_argument("--category", help="категорія TheMealDB, наприклад Breakfast")
        parser.add_argument("--user", help="адміністратор, якому створити чернетки")
        parser.add_argument("--delay", type=float, default=5, help="пауза між запитами до Gemini, секунд")

    def get_admin(self, username):
        users = get_user_model().objects.filter(is_staff=True)
        if username:
            users = users.filter(username=username)
        admin = users.order_by("pk").first()
        if admin is None:
            raise CommandError("Немає адміністратора. Створи його: python manage.py createsuperuser")
        return admin

    def handle(self, *args, **options):
        admin = self.get_admin(options["user"])
        known = set(RecipeImport.objects.exclude(external_id="").values_list("external_id", flat=True))
        session = requests.Session()
        created = 0
        skipped = []

        try:
            for meal in iter_meals(session, options["category"]):
                if created >= options["limit"]:
                    break
                key = external_id(meal["idMeal"])
                if key in known:
                    continue
                known.add(key)
                if created or skipped:
                    time.sleep(options["delay"])

                raw = meal_to_raw(meal)
                try:
                    data = parse_recipe(raw)
                except QuotaExceeded as exc:
                    self.stdout.write(self.style.WARNING(f"⚠️ {exc}"))
                    break
                except ImportFailed as exc:
                    self.stdout.write(self.style.WARNING(f"⚠️ {raw.title}: {exc}"))
                    skipped.append(raw.title)
                    continue

                RecipeImport.objects.create(user=admin, url=raw.url, external_id=key, data=data)
                created += 1
                self.stdout.write(f"✓ {raw.title} → {data['name']}")
        except ImportFailed as exc:
            self.stdout.write(self.style.WARNING(f"⚠️ {exc}"))

        self.stdout.write(self.style.SUCCESS(f"Нових чернеток: {created}, пропущено: {len(skipped)}"))
