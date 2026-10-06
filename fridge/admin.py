from django.contrib import admin
from django.utils.html import format_html

from .models import FridgeItem, Product


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "default_unit", "calories_per_100", "grams_per_piece", "usda_link")
    list_filter = ("default_unit",)
    search_fields = ("name",)

    @admin.display(description="USDA")
    def usda_link(self, obj):
        if not obj.usda_fdc_id:
            return "—"
        return format_html(
            '<a href="https://fdc.nal.usda.gov/food-details/{}/nutrients" target="_blank" rel="noopener">{}</a>',
            obj.usda_fdc_id,
            obj.usda_fdc_id,
        )


@admin.register(FridgeItem)
class FridgeItemAdmin(admin.ModelAdmin):
    list_display = ("product", "quantity", "user", "added_at", "expiry_date")
    list_filter = ("user",)
    search_fields = ("product__name",)
    autocomplete_fields = ("product",)