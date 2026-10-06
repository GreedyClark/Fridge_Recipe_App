from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Count, Sum
from django.db.models.functions import TruncDate
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView, ListView, TemplateView

from fridge.models import FridgeItem

from .models import CookingLog, Recipe
from .services import (
    ALMOST,
    MISSING,
    READY,
    fridge_stock,
    match_recipe,
    match_recipes,
    missing_products,
    write_off,
)

LIGHT_CALORIES = 500
LOG_LIMIT = 50
WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"]

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


class WhatCanICookView(LoginRequiredMixin, TemplateView):
    template_name = "recipes/what_can_i_cook.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        matches = match_recipes(self.request.user)
        context["ready"] = [match for match in matches if match.status == READY]
        context["almost"] = [match for match in matches if match.status == ALMOST]
        context["missing"] = [match for match in matches if match.status == MISSING]
        context["missing_products"] = missing_products(matches)
        context["product_count"] = (
            FridgeItem.objects.filter(user=self.request.user).values("product").distinct().count()
        )
        return context


class RecipeDetailView(LoginRequiredMixin, DetailView):
    template_name = "recipes/recipe_detail.html"

    def get_queryset(self):
        return Recipe.objects.visible_to(self.request.user).prefetch_related("ingredients__product")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["match"] = match_recipe(self.object, fridge_stock(self.request.user))
        return context


class CookRecipeView(LoginRequiredMixin, View):
    def post(self, request, pk):
        recipes = Recipe.objects.visible_to(request.user).prefetch_related("ingredients__product")
        recipe = get_object_or_404(recipes, pk=pk)
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
            required = [ingredient for ingredient in recipe.ingredients.all() if not ingredient.optional]
            for ingredient in required:
                write_off(request.user, ingredient.product, ingredient.quantity)

        written_off = ", ".join(str(ingredient) for ingredient in required)
        messages.success(request, f"Записано! З холодильника списано: {written_off}")
        return redirect("recipes:log")


class CookingLogListView(LoginRequiredMixin, ListView):
    template_name = "recipes/cooking_log.html"
    context_object_name = "logs"

    def get_queryset(self):
        logs = list(CookingLog.objects.filter(user=self.request.user).select_related("recipe")[:LOG_LIMIT])
        for log in logs:
            log.local_date = timezone.localdate(log.cooked_at)
        return logs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
        week_start = today - timedelta(days=6)

        rows = (
            CookingLog.objects.filter(user=self.request.user, cooked_at__date__gte=week_start)
            .annotate(day=TruncDate("cooked_at"))
            .values("day")
            .annotate(total=Sum("calories_consumed"), dishes=Count("id"))
        )
        by_day = {row["day"]: row for row in rows}

        days = []
        for offset in range(7):
            day = week_start + timedelta(days=offset)
            row = by_day.get(day, {})
            days.append({
                "date": day,
                "label": WEEKDAYS[day.weekday()],
                "total": round(row.get("total") or 0),
                "is_today": day == today,
            })

        peak = max(day["total"] for day in days)
        for day in days:
            day["height"] = round(day["total"] / peak * 100) if peak else 0

        week_total = sum(day["total"] for day in days)
        context.update({
            "today": today,
            "yesterday": today - timedelta(days=1),
            "today_total": days[-1]["total"],
            "today_dishes": by_day.get(today, {}).get("dishes", 0),
            "week_days": days,
            "week_total": week_total,
            "week_average": round(week_total / 7),
        })
        return context