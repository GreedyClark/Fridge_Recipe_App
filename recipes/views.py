from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect
from django.views import View
from django.views.generic import DetailView, TemplateView

from .models import CookingLog, Recipe
from .services import ALMOST, READY, fridge_stock, match_recipe, match_recipes, write_off

LIGHT_CALORIES = 500

FILTERS = [
    ("all", "Усі"),
    ("ready", "Можна приготувати"),
    ("almost", "Майже"),
    ("light", "До 500 ккал"),
]


def passes_filter(match, key):
    if key == "ready":
        return match.status == READY
    if key == "almost":
        return match.status == ALMOST
    if key == "light":
        return match.nutrition["calories"] <= LIGHT_CALORIES
    return True


def matches_query(match, query):
    names = [match.recipe.name] + [ingredient.product.name for ingredient in match.ingredients]
    return any(query in name.casefold() for name in names)


class RecipeListView(LoginRequiredMixin, TemplateView):
    template_name = "recipes/recipe_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        query = self.request.GET.get("q", "").strip()
        active = self.request.GET.get("filter", "all")
        if active not in dict(FILTERS):
            active = "all"

        matches = match_recipes(self.request.user)
        if query:
            matches = [match for match in matches if matches_query(match, query.casefold())]

        context["filters"] = [
            {
                "key": key,
                "label": label,
                "count": sum(1 for match in matches if passes_filter(match, key)),
                "active": key == active,
            }
            for key, label in FILTERS
        ]
        context["matches"] = [match for match in matches if passes_filter(match, active)]
        context["query"] = query
        context["active_filter"] = active
        return context


class RecipeDetailView(LoginRequiredMixin, DetailView):
    template_name = "recipes/recipe_detail.html"
    queryset = Recipe.objects.prefetch_related("ingredients__product")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["match"] = match_recipe(self.object, fridge_stock(self.request.user))
        return context


class CookRecipeView(LoginRequiredMixin, View):
    def post(self, request, pk):
        recipe = get_object_or_404(Recipe.objects.prefetch_related("ingredients__product"), pk=pk)
        match = match_recipe(recipe, fridge_stock(request.user))
        if match.status != READY:
            names = ", ".join(ingredient.product.name for ingredient in match.lacking)
            messages.error(request, f"Не вистачає інгредієнтів: {names}")
            return redirect("recipes:detail", pk=recipe.pk)

        with transaction.atomic():
            CookingLog.objects.create(
                user=request.user,
                recipe=recipe,
                calories_consumed=match.nutrition["calories"],
            )
            for ingredient in recipe.ingredients.all():
                write_off(request.user, ingredient.product, ingredient.quantity)

        written_off = ", ".join(str(ingredient) for ingredient in recipe.ingredients.all())
        messages.success(request, f"Записано! З холодильника списано: {written_off}")
        return redirect("recipes:log")