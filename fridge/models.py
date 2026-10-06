from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

UNIT_CHOICES = [
    ("g", "г"),
    ("ml", "мл"),
    ("pcs", "шт"),
]


class ProductManager(models.Manager):
    def get_by_natural_key(self, name):
        return self.get(name=name)


class Product(models.Model):
    name = models.CharField("назва", max_length=100, unique=True)
    default_unit = models.CharField("одиниця", max_length=3, choices=UNIT_CHOICES, default="g")
    calories_per_100 = models.FloatField("ккал на 100 г")
    protein_per_100 = models.FloatField("білки на 100 г", null=True, blank=True)
    fat_per_100 = models.FloatField("жири на 100 г", null=True, blank=True)
    carbs_per_100 = models.FloatField("вуглеводи на 100 г", null=True, blank=True)
    grams_per_piece = models.FloatField("вага 1 шт, г", null=True, blank=True)
    usda_fdc_id = models.IntegerField("USDA FDC ID", null=True, blank=True)
    usda_query = models.CharField("запит до USDA", max_length=255, blank=True)

    objects = ProductManager()

    class Meta:
        ordering = ["name"]
        verbose_name = "продукт"
        verbose_name_plural = "продукти"

    def __str__(self):
        return self.name

    def natural_key(self):
        return (self.name,)

    def clean(self):
        if self.default_unit == "pcs" and not self.grams_per_piece:
            raise ValidationError({"grams_per_piece": "Для штучних продуктів вкажіть вагу однієї штуки."})

    def grams(self, quantity):
        if self.default_unit == "pcs":
            return quantity * (self.grams_per_piece or 0)
        return quantity


class FridgeItem(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="fridge_items",
        verbose_name="користувач",
    )
    product = models.ForeignKey(Product, on_delete=models.PROTECT, verbose_name="продукт")
    quantity = models.FloatField("кількість", validators=[MinValueValidator(0.01)])
    added_at = models.DateTimeField("додано", auto_now_add=True)
    expiry_date = models.DateField("придатний до", null=True, blank=True)

    class Meta:
        ordering = ["product__name"]
        verbose_name = "продукт у холодильнику"
        verbose_name_plural = "продукти в холодильнику"

    def __str__(self):
        return f"{self.quantity:g} {self.unit} {self.product.name}"

    @property
    def unit(self):
        return self.product.get_default_unit_display()

    @property
    def expiry_status(self):
        if not self.expiry_date:
            return None
        days_left = (self.expiry_date - timezone.localdate()).days
        if days_left < 0:
            return "expired"
        if days_left <= 2:
            return "soon"
        return None