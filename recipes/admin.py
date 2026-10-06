from django.contrib import admin

from .models import CookingLog, Recipe, RecipeIngredient


class RecipeIngredientInline(admin.TabularInline):
    model = RecipeIngredient
    extra = 1
    autocomplete_fields = ("product",)


@admin.register(Recipe)
class RecipeAdmin(admin.ModelAdmin):
    list_display = ("name", "servings", "calories", "has_photo")
    search_fields = ("name",)
    inlines = [RecipeIngredientInline]

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("ingredients__product")

    @admin.display(description="ккал на порцію")
    def calories(self, obj):
        return obj.nutrition()["calories"]

    @admin.display(description="фото", boolean=True)
    def has_photo(self, obj):
        return bool(obj.image)


@admin.register(CookingLog)
class CookingLogAdmin(admin.ModelAdmin):
    list_display = ("recipe", "user", "cooked_at", "calories_consumed")
    list_filter = ("user",)
    date_hierarchy = "cooked_at"