from django.core.management.base import BaseCommand, CommandError

from fridge.catalog import PRODUCTS
from fridge.models import Product
from fridge.usda import USDAError, import_product, make_session


class Command(BaseCommand):
    help = "Імпортує калорійність і БЖУ продуктів з USDA FoodData Central"

    def add_arguments(self, parser):
        parser.add_argument("--only-new", action="store_true", help="пропустити продукти, які вже є в довіднику")

    def handle(self, *args, **options):
        try:
            session = make_session()
        except USDAError as exc:
            raise CommandError(str(exc)) from exc

        existing = set(Product.objects.values_list("name", flat=True)) if options["only_new"] else set()
        created = updated = 0
        failed = []
        for name, params in PRODUCTS.items():
            if name in existing:
                continue
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
                failed.append(name)
                continue

            if is_new:
                created += 1
            else:
                updated += 1
            self.stdout.write(self.style.SUCCESS(
                f'✅ {name} → {product.calories_per_100:g} ккал/100г '
                f'(FDC ID: {food.get("fdcId")}, матч: "{food.get("description")}")'
            ))

        self.stdout.write(f"\nСтворено: {created}, оновлено: {updated}, не знайдено: {len(failed)}")
        if failed:
            self.stdout.write("Не знайдено: " + ", ".join(failed))