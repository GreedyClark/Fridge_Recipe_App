from django.contrib import admin

from .models import CookingLog, Recipe, RecipeImport, RecipeIngredient


class RecipeIngredientInline(admin.TabularInline):
    model = RecipeIngredient
    extra = 1
    autocomplete_fields = ("product",)


@admin.register(Recipe)
class RecipeAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "servings", "calories", "has_photo")
    list_filter = ("owner",)
    search_fields = ("name",)
    inlines = [RecipeIngredientInline]

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("ingredients__product")

    @admin.display(description="ккал на порцію")
    def calories(self, obj):
        return obj.nutrition()["calories"]

    @admin.display(description="фото", boolean=True)
    def has_photo(self, obj):
        return bool(obj.image or obj.image_url)


@admin.register(CookingLog)
class CookingLogAdmin(admin.ModelAdmin):
    list_display = ("recipe", "user", "cooked_at", "calories_consumed")
    list_filter = ("user",)
    date_hierarchy = "cooked_at"


@admin.register(RecipeImport)
class RecipeImportAdmin(admin.ModelAdmin):
    list_display = ("url", "user", "status", "created_at")
    list_filter = ("status",)
    readonly_fields = ("user", "url", "status", "data", "error", "recipe", "created_at")

    def has_add_permission(self, request):
        return False