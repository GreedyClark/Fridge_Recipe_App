from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from .forms import FridgeItemForm
from .models import FridgeItem, Product

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
            for product in Product.objects.all()
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
                for product in Product.objects.filter(name__in=DEFAULT_POPULAR)
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