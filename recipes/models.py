from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

from fridge.models import Product

NUTRIENT_FIELDS = {
    "calories": "calories_per_100",
    "protein": "protein_per_100",
    "fat": "fat_per_100",
    "carbs": "carbs_per_100",
}


class Recipe(models.Model):
    name = models.CharField("назва", max_length=150)
    instructions = models.TextField("приготування", help_text="Кожен крок з нового рядка.")
    image = models.ImageField("фото", upload_to="recipes/", blank=True)
    servings = models.PositiveSmallIntegerField("порцій", default=1, validators=[MinValueValidator(1)])
    source_url = models.URLField("джерело", blank=True)
    created_at = models.DateTimeField("створено", auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "рецепт"
        verbose_name_plural = "рецепти"

    def __str__(self):
        return self.name

    @property
    def steps(self):
        return [line.strip() for line in self.instructions.splitlines() if line.strip()]

    def nutrition(self):
        totals = dict.fromkeys(NUTRIENT_FIELDS, 0.0)
        for ingredient in self.ingredients.all():
            product = ingredient.product
            factor = product.grams(ingredient.quantity) / 100
            for key, field in NUTRIENT_FIELDS.items():
                totals[key] += (getattr(product, field) or 0) * factor
        servings = self.servings or 1
        return {key: round(value / servings) for key, value in totals.items()}


class RecipeIngredient(models.Model):
    recipe = models.ForeignKey(
        Recipe,
        on_delete=models.CASCADE,
        related_name="ingredients",
        verbose_name="рецепт",
    )
    product = models.ForeignKey(Product, on_delete=models.PROTECT, verbose_name="продукт")
    quantity = models.FloatField("кількість", validators=[MinValueValidator(0.01)])

    class Meta:
        verbose_name = "інгредієнт"
        verbose_name_plural = "інгредієнти"
        constraints = [
            models.UniqueConstraint(fields=["recipe", "product"], name="unique_recipe_product"),
        ]

    def __str__(self):
        return f"{self.quantity:g} {self.product.get_default_unit_display()} {self.product.name}"


class CookingLog(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="cooking_logs",
        verbose_name="користувач",
    )
    recipe = models.ForeignKey(
        Recipe,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="рецепт",
    )
    cooked_at = models.DateTimeField("приготовано", auto_now_add=True)
    calories_consumed = models.FloatField("калорії")

    class Meta:
        ordering = ["-cooked_at"]
        verbose_name = "запис журналу"
        verbose_name_plural = "журнал"

    def __str__(self):
        name = self.recipe.name if self.recipe else "Видалений рецепт"
        return f"{name} — {self.calories_consumed:g} ккал"