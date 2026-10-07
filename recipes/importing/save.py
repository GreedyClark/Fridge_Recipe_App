from django.db import transaction

from fridge.models import Product
from fridge.usda import USDAError, import_product

from ..models import Recipe, RecipeImport, RecipeIngredient
from .errors import ImportFailed


class RowFailed(ImportFailed):
    def __init__(self, index, message):
        super().__init__(message)
        self.index = index


def resolve_products(rows):
    catalog = {product.name.casefold(): product for product in Product.objects.all()}
    products = []
    for index, row in enumerate(rows):
        product = row.get("product")
        if product is None:
            name = row["new_product_name"].strip()
            product = catalog.get(name.casefold())
        if product is None:
            try:
                product, _, _ = import_product(name, row["usda_query"], row["new_unit"] or "g", row["grams_per_piece"])
            except USDAError as exc:
                raise RowFailed(index, f"Не вдалося знайти «{name}» у базі USDA. Обери продукт зі списку.") from exc
            catalog[product.name.casefold()] = product
        if product in products:
            raise RowFailed(index, f"«{product.name}» уже є в іншому рядку.")
        products.append(product)
    return products


def save_import(record, recipe_data, rows, steps):
    products = resolve_products(rows)

    with transaction.atomic():
        record = RecipeImport.objects.select_for_update().get(pk=record.pk)
        if record.status != RecipeImport.DRAFT:
            raise ImportFailed("Цей рецепт уже збережено.")

        recipe = Recipe.objects.create(
            owner=record.user,
            name=recipe_data["name"],
            servings=recipe_data["servings"],
            instructions="\n".join(steps),
            image=recipe_data.get("image") or "",
            image_url=record.data.get("image_url", ""),
            source_url=record.data.get("source_url") or record.url,
        )
        RecipeIngredient.objects.bulk_create([
            RecipeIngredient(recipe=recipe, product=product, quantity=row["quantity"], optional=row["optional"])
            for product, row in zip(products, rows)
        ])

        record.status = RecipeImport.SAVED
        record.recipe = recipe
        record.save(update_fields=["status", "recipe"])
    return recipe


def update_recipe(recipe, recipe_data, rows, steps):
    products = resolve_products(rows)

    with transaction.atomic():
        recipe.name = recipe_data["name"]
        recipe.servings = recipe_data["servings"]
        recipe.instructions = "\n".join(steps)
        if recipe_data.get("image"):
            recipe.image = recipe_data["image"]
        recipe.save()

        recipe.ingredients.all().delete()
        RecipeIngredient.objects.bulk_create([
            RecipeIngredient(recipe=recipe, product=product, quantity=row["quantity"], optional=row["optional"])
            for product, row in zip(products, rows)
        ])
    return recipe