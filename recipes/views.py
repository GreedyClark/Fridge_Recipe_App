from datetime import timedelta
from urllib.parse import urlencode, urlparse

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import DeleteView, DetailView, FormView, ListView, TemplateView

from fridge.models import FridgeItem, Product

from .forms import UNIT_LABELS, ImportedIngredientFormSet, ImportedRecipeForm, RecipeImportForm
from .importing.errors import ImportFailed
from .importing.pipeline import run_import
from .importing.save import RowFailed, save_import, update_recipe
from .models import CookingLog, Recipe, RecipeImport
from .services import (
    ALMOST,
    MISSING,
    READY,
    fridge_stock,
    match_recipe,
    match_recipes,
    max_servings,
    missing_products,
    parse_servings,
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
    ("mine", "Мої"),
]


def passes_filter(match, key, user):
    if key == "mine":
        return match.recipe.owner_id == user.pk
    if key == "ready":
        return match.status == READY
    if key == "almost":
        return match.status == ALMOST
    if key == "light":
        return match.nutrition["calories"] <= LIGHT_CALORIES
    return True


MEALS = [("all", "Будь-коли"), *Recipe.MEAL_CHOICES]


def passes_meal(match, key):
    return key == "all" or match.recipe.meal_type == key


def selected(request, name, options):
    value = request.GET.get(name, "all")
    return value if value in dict(options) else "all"


def page_url(**params):
    query = {key: value for key, value in params.items() if value and value != "all"}
    return f"?{urlencode(query)}" if query else "?"


def meal_tabs(matches, active, **params):
    return [
        {
            "label": label,
            "count": sum(1 for match in matches if passes_meal(match, key)),
            "active": key == active,
            "url": page_url(**params, meal=key),
        }
        for key, label in MEALS
    ]


def reviewable_imports(user):
    imports = RecipeImport.objects.filter(status=RecipeImport.DRAFT)
    if user.is_staff:
        return imports.filter(Q(user=user) | ~Q(external_id=""))
    return imports.filter(user=user)


def shared_drafts():
    return RecipeImport.objects.filter(status=RecipeImport.DRAFT).exclude(external_id="")


def matches_query(match, query):
    names = [match.recipe.name] + [ingredient.product.name for ingredient in match.ingredients]
    return any(query in name.casefold() for name in names)


class RecipeListView(LoginRequiredMixin, TemplateView):
    template_name = "recipes/recipe_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        query = self.request.GET.get("q", "").strip()
        active = selected(self.request, "filter", FILTERS)
        meal = selected(self.request, "meal", MEALS)

        matches = match_recipes(user)
        if query:
            matches = [match for match in matches if matches_query(match, query.casefold())]
        by_meal = [match for match in matches if passes_meal(match, meal)]
        by_filter = [match for match in matches if passes_filter(match, active, user)]

        context["filters"] = [
            {
                "label": label,
                "count": sum(1 for match in by_meal if passes_filter(match, key, user)),
                "active": key == active,
                "url": page_url(q=query, filter=key, meal=meal),
            }
            for key, label in FILTERS
        ]
        context["meals"] = meal_tabs(by_filter, meal, q=query, filter=active)
        context["matches"] = [match for match in by_filter if passes_meal(match, meal)]
        context["query"] = query
        context["active_filter"] = active
        context["active_meal"] = meal
        if user.is_staff:
            context["draft_count"] = shared_drafts().count()
        return context


class WhatCanICookView(LoginRequiredMixin, TemplateView):
    template_name = "recipes/what_can_i_cook.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        meal = selected(self.request, "meal", MEALS)
        all_matches = match_recipes(self.request.user)
        matches = [match for match in all_matches if passes_meal(match, meal)]
        context["meals"] = meal_tabs(all_matches, meal)
        context["active_meal"] = meal
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
        recipe = self.object
        servings = parse_servings(self.request.GET.get("servings"), recipe)
        context["match"] = match_recipe(recipe, fridge_stock(self.request.user), servings)
        context["servings"] = servings
        context["less_url"] = f"?servings={servings - 1}" if servings > 1 else ""
        context["more_url"] = f"?servings={servings + 1}" if servings < max_servings(recipe) else ""
        return context


class RecipeDeleteView(LoginRequiredMixin, DeleteView):
    template_name = "recipes/recipe_confirm_delete.html"
    success_url = reverse_lazy("recipes:list")

    def get_queryset(self):
        return Recipe.objects.filter(owner=self.request.user)

    def form_valid(self, form):
        messages.success(self.request, f"Рецепт «{self.object.name}» видалено.")
        return super().form_valid(form)


class CookRecipeView(LoginRequiredMixin, View):
    def post(self, request, pk):
        recipes = Recipe.objects.visible_to(request.user).prefetch_related("ingredients__product")
        recipe = get_object_or_404(recipes, pk=pk)
        servings = parse_servings(request.POST.get("servings"), recipe)
        eaten = min(parse_servings(request.POST.get("eaten"), recipe), servings) if request.POST.get("eaten") else 1
        detail_url = f"{reverse('recipes:detail', args=[recipe.pk])}?servings={servings}"

        match = match_recipe(recipe, fridge_stock(request.user), servings)
        if match.status != READY:
            names = ", ".join(ingredient.product.name for ingredient in match.lacking)
            messages.error(request, f"Не вистачає інгредієнтів: {names}")
            return redirect(detail_url)

        with transaction.atomic():
            CookingLog.objects.create(
                user=request.user,
                recipe=recipe,
                servings=eaten,
                calories_consumed=match.nutrition["calories"] * eaten,
            )
            used = [ingredient for ingredient in match.required if ingredient.needed is not None]
            for ingredient in used:
                write_off(request.user, ingredient.product, ingredient.needed)

        written_off = ", ".join(
            f"{round(ingredient.needed, 2):g} {ingredient.product.get_default_unit_display()} {ingredient.product.name}"
            for ingredient in used
        )
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


def bind_editor(request, catalog_user, allow_shared=False):
    recipe_form = ImportedRecipeForm(request.POST, request.FILES, allow_shared=allow_shared)
    formset = ImportedIngredientFormSet(request.POST, prefix="ingredients", form_kwargs={"user": catalog_user})
    steps = [step.strip() for step in request.POST.getlist("steps") if step.strip()]

    recipe_valid = recipe_form.is_valid()
    formset_valid = formset.is_valid()
    if not steps:
        recipe_form.add_error(None, "Додай хоча б один крок приготування.")
    return recipe_form, formset, steps, recipe_valid and formset_valid and bool(steps)


def save_editor(save, recipe_form, formset, steps):
    kept = formset.kept_forms()
    try:
        return save(recipe_form.cleaned_data, [form.cleaned_data for form in kept], steps)
    except RowFailed as exc:
        kept[exc.index].add_error("product", str(exc))
    except ImportFailed as exc:
        recipe_form.add_error(None, str(exc))
    return None


def editor_context(recipe_form, formset, steps, user):
    units = {str(product.pk): product.get_default_unit_display() for product in Product.objects.visible_to(user)}
    for form in formset.forms:
        product_id = str(form.value_of("product"))
        form.unit = units.get(product_id) or UNIT_LABELS.get(form.value_of("new_unit"), "—")
    return {
        "recipe_form": recipe_form,
        "formset": formset,
        "steps": steps or [""],
        "has_errors": bool(recipe_form.errors or formset.total_error_count()),
        "product_units": units,
    }


class ImportReviewView(LoginRequiredMixin, View):
    template_name = "recipes/import_review.html"

    def get_record(self):
        return get_object_or_404(reviewable_imports(self.request.user), pk=self.kwargs["pk"])

    def catalog_user(self, record):
        return None if record.external_id else self.request.user

    def get(self, request, pk):
        record = self.get_record()
        data = record.data
        recipe_form = ImportedRecipeForm(
            initial={
                "name": data.get("name"),
                "meal_type": data.get("meal_type", ""),
                "servings": data.get("servings"),
                "shared": bool(record.external_id),
            },
            allow_shared=request.user.is_staff,
        )
        rows = data.get("ingredients", [])
        formset = ImportedIngredientFormSet(
            prefix="ingredients",
            initial=[ingredient_initial(row) for row in rows],
            form_kwargs={"user": self.catalog_user(record)},
        )
        for form, row in zip(formset.forms, rows):
            form.warning = row.get("warning", "")
        return self.render(record, recipe_form, formset, data.get("steps", []))

    def post(self, request, pk):
        record = self.get_record()
        recipe_form, formset, steps, valid = bind_editor(
            request, self.catalog_user(record), allow_shared=request.user.is_staff
        )
        if valid:
            shared = recipe_form.cleaned_data.get("shared", False)
            recipe = save_editor(
                lambda data, rows, steps: save_import(record, data, rows, steps, shared=shared),
                recipe_form,
                formset,
                steps,
            )
            if recipe:
                if shared:
                    messages.success(request, f"Рецепт «{recipe.name}» додано в спільну базу.")
                else:
                    messages.success(request, f"Рецепт «{recipe.name}» збережено. Його бачиш лише ти.")
                return redirect("recipes:detail", pk=recipe.pk)
        return self.render(record, recipe_form, formset, steps or request.POST.getlist("steps"))

    def render(self, record, recipe_form, formset, steps):
        source_url = record.data.get("source_url") or record.url
        context = editor_context(recipe_form, formset, steps, self.catalog_user(record))
        context.update({
            "record": record,
            "warning_count": sum(1 for form in formset.forms if form.warning),
            "image_url": record.data.get("image_url", ""),
            "source_url": source_url,
            "source_domain": urlparse(source_url).netloc.removeprefix("www."),
            "side_template": "recipes/_import_side.html",
            "submit_label": "Зберегти рецепт",
            "cancel_url": reverse("recipes:drafts" if record.external_id else "recipes:import"),
        })
        return render(self.request, self.template_name, context)


class StaffRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return self.request.user.is_staff


class SharedDraftListView(StaffRequiredMixin, ListView):
    template_name = "recipes/shared_drafts.html"
    context_object_name = "drafts"

    def get_queryset(self):
        drafts = list(shared_drafts().order_by("created_at"))
        meals = dict(Recipe.MEAL_CHOICES)
        for draft in drafts:
            rows = draft.data.get("ingredients", [])
            draft.ingredient_count = len(rows)
            draft.warning_count = sum(1 for row in rows if row.get("warning"))
            draft.meal_label = meals.get(draft.data.get("meal_type"), "")
        return drafts


class RejectDraftView(StaffRequiredMixin, View):
    def post(self, request, pk):
        draft = get_object_or_404(shared_drafts(), pk=pk)
        draft.status = RecipeImport.REJECTED
        draft.save(update_fields=["status"])
        messages.info(request, f"Чернетку «{draft.data.get('name', draft.url)}» відхилено.")
        return redirect("recipes:drafts")


class RecipeEditView(LoginRequiredMixin, View):
    template_name = "recipes/recipe_edit.html"

    def get_recipe(self):
        recipes = Recipe.objects.filter(owner=self.request.user).prefetch_related("ingredients__product")
        return get_object_or_404(recipes, pk=self.kwargs["pk"])

    def get(self, request, pk):
        recipe = self.get_recipe()
        recipe_form = ImportedRecipeForm(
            initial={"name": recipe.name, "meal_type": recipe.meal_type, "servings": recipe.servings}
        )
        rows = [
            {"product_id": ingredient.product_id, "quantity": ingredient.quantity, "optional": ingredient.optional}
            for ingredient in recipe.ingredients.all()
        ]
        formset = ImportedIngredientFormSet(
            prefix="ingredients",
            initial=[ingredient_initial(row) for row in rows],
            form_kwargs={"user": request.user},
        )
        return self.render(recipe, recipe_form, formset, recipe.steps)

    def post(self, request, pk):
        recipe = self.get_recipe()
        recipe_form, formset, steps, valid = bind_editor(request, request.user)
        if valid:
            saved = save_editor(
                lambda data, rows, steps: update_recipe(recipe, data, rows, steps), recipe_form, formset, steps
            )
            if saved:
                messages.success(request, "Зміни збережено.")
                return redirect("recipes:detail", pk=recipe.pk)
        return self.render(recipe, recipe_form, formset, steps or request.POST.getlist("steps"))

    def render(self, recipe, recipe_form, formset, steps):
        context = editor_context(recipe_form, formset, steps, self.request.user)
        context.update({
            "recipe": recipe,
            "editing": True,
            "side_template": "recipes/_edit_side.html",
            "submit_label": "Зберегти зміни",
            "cancel_url": reverse("recipes:detail", args=[recipe.pk]),
        })
        return render(self.request, self.template_name, context)