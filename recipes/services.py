from dataclasses import dataclass, field

from django.db.models import Sum

from fridge.models import FridgeItem, Product

from .models import Recipe

READY = "ready"
ALMOST = "almost"
MISSING = "missing"
EPSILON = 1e-6
MAX_SERVINGS = 10


@dataclass
class IngredientCheck:
    product: object
    needed: float | None
    available: float
    optional: bool = False

    @property
    def shortage(self):
        if self.needed is None:
            return 0
        return max(self.needed - self.available, 0)

    @property
    def status(self):
        if self.available <= EPSILON:
            return MISSING
        if self.shortage > EPSILON:
            return ALMOST
        return READY


@dataclass
class RecipeMatch:
    recipe: Recipe
    ingredients: list
    servings: int = 0
    nutrition: dict = field(init=False)

    def __post_init__(self):
        self.servings = self.servings or self.recipe.servings
        self.nutrition = self.recipe.nutrition()

    @property
    def required(self):
        return [ingredient for ingredient in self.ingredients if not ingredient.optional]

    @property
    def to_taste(self):
        return [ingredient for ingredient in self.ingredients if ingredient.optional]

    @property
    def status(self):
        statuses = {ingredient.status for ingredient in self.required}
        if MISSING in statuses:
            return MISSING
        if ALMOST in statuses:
            return ALMOST
        return READY

    @property
    def ready_count(self):
        return sum(1 for ingredient in self.required if ingredient.status == READY)

    @property
    def lacking(self):
        return [ingredient for ingredient in self.required if ingredient.status != READY]


@dataclass
class MissingProduct:
    product: object
    available: float
    shortage: float = 0
    unlocks: int = 0

    @property
    def to_buy(self):
        if self.shortage > EPSILON:
            return self.shortage
        return 1 if self.product.default_unit == "pcs" else 100


def fridge_stock(user):
    rows = (
        FridgeItem.objects.filter(user=user)
        .values("product")
        .annotate(total=Sum("quantity"))
        .values_list("product", "total")
    )
    return dict(rows)


def max_servings(recipe):
    return max(MAX_SERVINGS, recipe.servings)


def parse_servings(value, recipe):
    try:
        servings = int(value)
    except (TypeError, ValueError):
        return recipe.servings
    return min(max(servings, 1), max_servings(recipe))


def scaled(quantity, servings, recipe):
    if quantity is None:
        return None
    return quantity * servings / (recipe.servings or 1)


def match_recipe(recipe, stock, servings=None):
    servings = servings or recipe.servings
    checks = [
        IngredientCheck(
            product=ingredient.product,
            needed=scaled(ingredient.quantity, servings, recipe),
            available=stock.get(ingredient.product_id, 0),
            optional=ingredient.optional,
        )
        for ingredient in recipe.ingredients.all()
    ]
    return RecipeMatch(recipe=recipe, ingredients=checks, servings=servings)


def match_recipes(user):
    stock = fridge_stock(user)
    recipes = Recipe.objects.visible_to(user).prefetch_related("ingredients__product")
    return [match_recipe(recipe, stock) for recipe in recipes]


def missing_products(matches, limit=10):
    found = {}
    for match in matches:
        lacking = match.lacking
        for ingredient in lacking:
            item = found.setdefault(
                ingredient.product.pk,
                MissingProduct(product=ingredient.product, available=ingredient.available),
            )
            item.shortage = max(item.shortage, ingredient.shortage)
            if len(lacking) == 1:
                item.unlocks += 1
    items = sorted(found.values(), key=lambda item: (-item.unlocks, item.product.name))
    return items[:limit]


def write_off(user, product, quantity):
    remaining = quantity
    taken = []
    items = FridgeItem.objects.select_for_update().filter(user=user, product=product).order_by("added_at")
    for item in items:
        if remaining <= EPSILON:
            break
        used = min(item.quantity, remaining)
        taken.append({
            "product": product.pk,
            "quantity": used,
            "expiry_date": item.expiry_date.isoformat() if item.expiry_date else None,
        })
        item.quantity -= used
        remaining -= used
        if item.quantity <= EPSILON:
            item.delete()
        else:
            item.save(update_fields=["quantity"])
    return taken


def restore(user, written_off):
    products = Product.objects.in_bulk({row["product"] for row in written_off})
    for row in written_off:
        if row["product"] in products:
            FridgeItem.add(user, products[row["product"]], row["quantity"], row["expiry_date"])


def written_off_summary(written_off):
    totals = {}
    for row in written_off:
        totals[row["product"]] = totals.get(row["product"], 0) + row["quantity"]
    products = Product.objects.in_bulk(totals)
    return ", ".join(
        f"{products[pk].name} {round(quantity, 2):g} {products[pk].get_default_unit_display()}"
        for pk, quantity in totals.items()
        if pk in products
    )