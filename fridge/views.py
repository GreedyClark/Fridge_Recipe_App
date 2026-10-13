from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.generic import CreateView, DeleteView, FormView, ListView, UpdateView

from recipes.importing.errors import ImportFailed
from recipes.importing.gemini import usda_query_for

from .forms import BuyForm, FridgeItemForm, ProductCreateForm, ShoppingItemForm
from .models import FridgeItem, Product, ShoppingItem
from .usda import USDAError, import_product

POPULAR_LIMIT = 5
DEFAULT_POPULAR = ["Яйце куряче", "Молоко 2.5%", "Хліб пшеничний", "Банан", "Картопля"]


class UserFridgeMixin(LoginRequiredMixin):
    def get_queryset(self):
        return FridgeItem.objects.filter(user=self.request.user).select_related("product")


class FridgeListView(UserFridgeMixin, ListView):
    template_name = "fridge/fridge_list.html"
    context_object_name = "items"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["expiring"] = [item for item in context["items"] if item.expiry_status]
        return context


class FridgeFormMixin(UserFridgeMixin):
    form_class = FridgeItemForm
    template_name = "fridge/fridge_form.html"
    success_url = reverse_lazy("fridge:list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["product_data"] = {
            str(product.pk): {
                "unit": product.get_default_unit_display(),
                "calories": product.calories_per_100,
                "protein": product.protein_per_100,
                "fat": product.fat_per_100,
                "carbs": product.carbs_per_100,
            }
            for product in Product.objects.visible_to(self.request.user)
        }
        context["popular_products"] = self.get_popular_products()
        return context

    def get_popular_products(self):
        popular = list(
            Product.objects.filter(fridgeitem__user=self.request.user)
            .annotate(times=Count("fridgeitem"))
            .order_by("-times", "name")[:POPULAR_LIMIT]
        )
        if len(popular) < POPULAR_LIMIT:
            taken = {product.pk for product in popular}
            defaults = [
                product
                for product in Product.objects.shared().filter(name__in=DEFAULT_POPULAR)
                if product.pk not in taken
            ]
            defaults.sort(key=lambda product: DEFAULT_POPULAR.index(product.name))
            popular += defaults[: POPULAR_LIMIT - len(popular)]
        return popular


class FridgeCreateView(FridgeFormMixin, CreateView):
    def get_initial(self):
        initial = super().get_initial()
        product_id = self.request.GET.get("product", "")
        if product_id.isdigit():
            initial["product"] = int(product_id)
        return initial

    def form_valid(self, form):
        form.instance.user = self.request.user
        messages.success(self.request, f"Додано: {form.instance}")
        return super().form_valid(form)


class FridgeUpdateView(FridgeFormMixin, UpdateView):
    def form_valid(self, form):
        messages.success(self.request, f"Збережено: {form.instance}")
        return super().form_valid(form)


class FridgeDeleteView(UserFridgeMixin, DeleteView):
    template_name = "fridge/fridge_confirm_delete.html"
    context_object_name = "item"
    success_url = reverse_lazy("fridge:list")

    def form_valid(self, form):
        messages.success(self.request, f"Видалено: {self.object.product.name}")
        return super().form_valid(form)



class ProductCreateView(LoginRequiredMixin, FormView):
    template_name = "fridge/product_form.html"
    form_class = ProductCreateForm

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        data = form.cleaned_data
        try:
            query = usda_query_for(data["name"])
            product, _, _ = import_product(
                data["name"], query, data["unit"], data["grams_per_piece"], owner=self.request.user
            )
        except ImportFailed as exc:
            form.add_error("name", str(exc))
            return self.form_invalid(form)
        except USDAError:
            form.add_error("name", f"Не знайшли «{data['name']}» у базі USDA. Спробуй іншу, простішу назву.")
            return self.form_invalid(form)

        messages.success(
            self.request, f"Продукт «{product.name}» додано: {product.calories_per_100:g} ккал на 100 г."
        )
        return redirect(f"{reverse('fridge:add')}?product={product.pk}")


def safe_next(request, default="fridge:shopping"):
    target = request.POST.get("next", "")
    if url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return target
    return reverse(default)


class ShoppingListView(LoginRequiredMixin, ListView):
    template_name = "fridge/shopping_list.html"
    context_object_name = "items"

    def get_queryset(self):
        return ShoppingItem.objects.filter(user=self.request.user).select_related("product")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        items = context["items"]
        context["pending"] = [item for item in items if not item.bought]
        context["bought"] = [item for item in items if item.bought]
        context["form"] = kwargs.get("form") or ShoppingItemForm(user=self.request.user)
        return context


class ShoppingAddView(LoginRequiredMixin, View):
    def post(self, request):
        form = ShoppingItemForm(request.POST, user=request.user)
        if not form.is_valid():
            for errors in form.errors.values():
                messages.error(request, " ".join(errors))
            return redirect(safe_next(request))
        product = form.cleaned_data["product"]
        item = ShoppingItem.add(
            request.user, product, form.cleaned_data["quantity"], request.POST.get("source", "вручну")[:200]
        )
        messages.success(request, f"«{product.name}» у списку покупок: {item.quantity:g} {item.unit}.")
        return redirect(safe_next(request))


class ShoppingBuyView(LoginRequiredMixin, View):
    def post(self, request, pk):
        item = get_object_or_404(ShoppingItem.objects.pending(), pk=pk, user=request.user)
        form = BuyForm(request.POST)
        expiry_date = form.cleaned_data["expiry_date"] if form.is_valid() else None
        with transaction.atomic():
            FridgeItem.objects.create(
                user=request.user, product=item.product, quantity=item.quantity, expiry_date=expiry_date
            )
            item.bought = True
            item.save(update_fields=["bought"])
        messages.success(request, f"«{item.product.name}» додано в холодильник: {item.quantity:g} {item.unit}.")
        return redirect("fridge:shopping")


class ShoppingDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        get_object_or_404(ShoppingItem, pk=pk, user=request.user).delete()
        return redirect("fridge:shopping")


class ShoppingClearView(LoginRequiredMixin, View):
    def post(self, request):
        ShoppingItem.objects.filter(user=request.user, bought=True).delete()
        return redirect("fridge:shopping")
