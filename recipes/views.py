from datetime import timedelta
from urllib.parse import urlparse

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Count, Sum
from django.db.models.functions import TruncDate
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView, FormView, ListView, TemplateView

from fridge.models import FridgeItem, Product

from .forms import UNIT_LABELS, ImportedIngredientFormSet, ImportedRecipeForm, RecipeImportForm
from .importing.errors import ImportFailed
from .importing.pipeline import run_import
from .importing.save import RowFailed, save_import
from .models import CookingLog, Recipe, RecipeImport
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
RECENT_IMPORTS = 5
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


class RecipeImportView(LoginRequiredMixin, FormView):
    template_name = "recipes/recipe_import.html"
    form_class = RecipeImportForm

    def imports_today(self):
        day_start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
        return self.request.user.recipe_imports.filter(created_at__gte=day_start).count()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["imports_today"] = self.imports_today()
        context["imports_limit"] = settings.IMPORTS_PER_DAY
        context["recent_imports"] = self.request.user.recipe_imports.select_related("recipe")[:RECENT_IMPORTS]
        return context

    def form_valid(self, form):
        url = form.cleaned_data["url"]
        user = self.request.user

        recipe = Recipe.objects.filter(owner=user, source_url=url).first()
        if recipe:
            messages.info(self.request, "Цей рецепт уже є у твоїй книзі.")
            return redirect("recipes:detail", pk=recipe.pk)

        draft = user.recipe_imports.filter(url=url, status=RecipeImport.DRAFT).first()
        if draft:
            messages.info(self.request, "Цей рецепт уже розібрано. Перевір чернетку.")
            return redirect("recipes:import_review", pk=draft.pk)

        if self.imports_today() >= settings.IMPORTS_PER_DAY:
            form.add_error("url", f"На сьогодні ліміт вичерпано ({settings.IMPORTS_PER_DAY} імпортів). Спробуй завтра.")
            return self.form_invalid(form)

        record = RecipeImport.objects.create(user=user, url=url)
        try:
            run_import(record)
        except ImportFailed as exc:
            form.add_error("url", str(exc))
            return self.form_invalid(form)
        return redirect("recipes:import_review", pk=record.pk)


def ingredient_initial(row):
    quantity = row.get("quantity")
    if quantity is not None and float(quantity).is_integer():
        quantity = int(quantity)
    return {
        "original": row.get("original", ""),
        "product": row.get("product_id"),
        "new_product_name": row.get("new_product_name", ""),
        "usda_query": row.get("usda_query", ""),
        "new_unit": row.get("unit", "g"),
        "grams_per_piece": row.get("grams_per_piece"),
        "quantity": quantity,
        "optional": row.get("optional", False),
    }


class ImportReviewView(LoginRequiredMixin, View):
    template_name = "recipes/import_review.html"

    def get_record(self):
        return get_object_or_404(self.request.user.recipe_imports, pk=self.kwargs["pk"], status=RecipeImport.DRAFT)

    def get(self, request, pk):
        record = self.get_record()
        data = record.data
        recipe_form = ImportedRecipeForm(initial={"name": data.get("name"), "servings": data.get("servings")})
        rows = data.get("ingredients", [])
        formset = ImportedIngredientFormSet(prefix="ingredients", initial=[ingredient_initial(row) for row in rows])
        for form, row in zip(formset.forms, rows):
            form.warning = row.get("warning", "")
        return self.render(record, recipe_form, formset, data.get("steps", []))

    def post(self, request, pk):
        record = self.get_record()
        recipe_form = ImportedRecipeForm(request.POST, request.FILES)
        formset = ImportedIngredientFormSet(request.POST, prefix="ingredients")
        steps = [step.strip() for step in request.POST.getlist("steps") if step.strip()]

        recipe_valid = recipe_form.is_valid()
        formset_valid = formset.is_valid()
        forms_valid = recipe_valid and formset_valid
        if not steps:
            recipe_form.add_error(None, "Додай хоча б один крок приготування.")

        if forms_valid and steps:
            kept = formset.kept_forms()
            try:
                recipe = save_import(record, recipe_form.cleaned_data, [form.cleaned_data for form in kept], steps)
            except RowFailed as exc:
                kept[exc.index].add_error("product", str(exc))
            except ImportFailed as exc:
                recipe_form.add_error(None, str(exc))
            else:
                messages.success(request, f"Рецепт «{recipe.name}» збережено. Його бачиш лише ти.")
                return redirect("recipes:detail", pk=recipe.pk)

        return self.render(record, recipe_form, formset, steps or request.POST.getlist("steps"))

    def render(self, record, recipe_form, formset, steps):
        units = {str(product.pk): product.get_default_unit_display() for product in Product.objects.all()}
        for form in formset.forms:
            product_id = str(form.value_of("product"))
            form.unit = units.get(product_id) or UNIT_LABELS.get(form.value_of("new_unit"), "—")
        source_url = record.data.get("source_url") or record.url
        context = {
            "record": record,
            "recipe_form": recipe_form,
            "formset": formset,
            "steps": steps or [""],
            "warning_count": sum(1 for form in formset.forms if form.warning),
            "has_errors": bool(recipe_form.errors or formset.total_error_count()),
            "image_url": record.data.get("image_url", ""),
            "source_url": source_url,
            "source_domain": urlparse(source_url).netloc.removeprefix("www."),
            "product_units": units,
        }
        return render(self.request, self.template_name, context)