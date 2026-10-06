from dataclasses import dataclass, field

from django.db.models import Sum

from fridge.models import FridgeItem

from .models import Recipe

READY = "ready"
ALMOST = "almost"
MISSING = "missing"
EPSILON = 1e-6


@dataclass
class IngredientCheck:
    product: object
    needed: float
    available: float

    @property
    def shortage(self):
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
    nutrition: dict = field(init=False)

    def __post_init__(self):
        self.nutrition = self.recipe.nutrition()

    @property
    def status(self):
        statuses = {ingredient.status for ingredient in self.ingredients}
        if MISSING in statuses:
            return MISSING
        if ALMOST in statuses:
            return ALMOST
        return READY

    @property
    def ready_count(self):
        return sum(1 for ingredient in self.ingredients if ingredient.status == READY)

    @property
    def lacking(self):
        return [ingredient for ingredient in self.ingredients if ingredient.status != READY]


def fridge_stock(user):
    rows = (
        FridgeItem.objects.filter(user=user)
        .values("product")
        .annotate(total=Sum("quantity"))
        .values_list("product", "total")
    )
    return dict(rows)


def match_recipe(recipe, stock):
    checks = [
        IngredientCheck(
            product=ingredient.product,
            needed=ingredient.quantity,
            available=stock.get(ingredient.product_id, 0),
        )
        for ingredient in recipe.ingredients.all()
    ]
    return RecipeMatch(recipe=recipe, ingredients=checks)


def match_recipes(user):
    stock = fridge_stock(user)
    recipes = Recipe.objects.prefetch_related("ingredients__product")
    return [match_recipe(recipe, stock) for recipe in recipes]


def write_off(user, product, quantity):
    remaining = quantity
    items = FridgeItem.objects.select_for_update().filter(user=user, product=product).order_by("added_at")
    for item in items:
        if remaining <= EPSILON:
            break
        used = min(item.quantity, remaining)
        item.quantity -= used
        remaining -= used
        if item.quantity <= EPSILON:
            item.delete()
        else:
            item.save(update_fields=["quantity"])



@dataclass
class MissingProduct:
    product: object
    available: float
    shortage: float = 0
    unlocks: int = 0


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