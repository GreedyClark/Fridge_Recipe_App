from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from accounts.models import people_ids

UNIT_CHOICES = [
    ("g", "г"),
    ("ml", "мл"),
    ("pcs", "шт"),
]
UNIT_LABELS = dict(UNIT_CHOICES)
BIG_UNITS = {"g": "кг", "ml": "л"}
QUANTITY_STEPS = {"g": 50, "ml": 100, "pcs": 1}


def format_quantity(quantity, unit):
    if unit in BIG_UNITS and quantity >= 1000:
        quantity, label = quantity / 1000, BIG_UNITS[unit]
    else:
        label = UNIT_LABELS.get(unit, unit)
    number = f"{quantity:.2f}".rstrip("0").rstrip(".").replace(".", ",")
    return f"{number} {label}"


class ProductQuerySet(models.QuerySet):
    def shared(self):
        return self.filter(owner__isnull=True)

    def visible_to(self, user):
        return self.filter(Q(owner__isnull=True) | Q(owner__in=people_ids(user)))


class ProductManager(models.Manager.from_queryset(ProductQuerySet)):
    def get_by_natural_key(self, name):
        return self.get(name=name, owner__isnull=True)


class Product(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="products",
        verbose_name="власник",
    )
    name = models.CharField("назва", max_length=100)
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
        constraints = [
            models.UniqueConstraint(fields=["name"], condition=Q(owner__isnull=True), name="unique_shared_product_name"),
            models.UniqueConstraint(fields=["owner", "name"], name="unique_private_product_name"),
        ]

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

    @classmethod
    def add(cls, user, product, quantity, expiry_date=None):
        item = (
            cls.objects.filter(user__in=people_ids(user), product=product, expiry_date=expiry_date)
            .order_by("added_at")
            .first()
        )
        if item:
            item.quantity += quantity
            item.save(update_fields=["quantity"])
            return item, False
        return cls.objects.create(user=user, product=product, quantity=quantity, expiry_date=expiry_date), True

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

class ShoppingItemQuerySet(models.QuerySet):
    def pending(self):
        return self.filter(bought=False)

    def for_people(self, user):
        return self.filter(user__in=people_ids(user))


class ShoppingItem(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="shopping_items",
        verbose_name="користувач",
    )
    product = models.ForeignKey(Product, on_delete=models.CASCADE, verbose_name="продукт")
    quantity = models.FloatField("кількість", validators=[MinValueValidator(0.01)])
    source = models.CharField("звідки", max_length=200, blank=True)
    taken_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="хто купує",
    )
    bought = models.BooleanField("куплено", default=False)
    bought_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="хто купив",
    )
    created_at = models.DateTimeField("додано", auto_now_add=True)

    objects = ShoppingItemQuerySet.as_manager()

    class Meta:
        ordering = ["bought", "created_at"]
        verbose_name = "покупка"
        verbose_name_plural = "список покупок"

    def __str__(self):
        return f"{self.quantity:g} {self.unit} {self.product.name}"

    @property
    def unit(self):
        return self.product.get_default_unit_display()

    @property
    def quantity_label(self):
        return format_quantity(self.quantity, self.product.default_unit)

    @property
    def step(self):
        return QUANTITY_STEPS.get(self.product.default_unit, 1)

    def change_quantity(self, direction):
        if direction == "more":
            self.quantity += self.step
        elif direction == "less" and self.quantity > self.step:
            self.quantity -= self.step
        else:
            return False
        self.quantity = round(self.quantity, 2)
        self.save(update_fields=["quantity"])
        return True

    @classmethod
    def add(cls, user, product, quantity, source=""):
        item = cls.objects.pending().for_people(user).filter(product=product).first()
        if item:
            item.quantity += quantity
            item.save(update_fields=["quantity"])
            return item
        return cls.objects.create(user=user, product=product, quantity=quantity, source=source)


def buy_items(user, items, expiry_date=None):
    for item in items:
        FridgeItem.add(user, item.product, item.quantity, expiry_date)
        item.bought = True
        item.bought_by = user
        item.save(update_fields=["bought", "bought_by"])
