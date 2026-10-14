from django.urls import path

from . import views

app_name = "fridge"

urlpatterns = [
    path("", views.FridgeListView.as_view(), name="list"),
    path("add/", views.FridgeCreateView.as_view(), name="add"),
    path("products/new/", views.ProductCreateView.as_view(), name="product_new"),
    path("<int:pk>/edit/", views.FridgeUpdateView.as_view(), name="edit"),
    path("<int:pk>/delete/", views.FridgeDeleteView.as_view(), name="delete"),
    path("shopping/", views.ShoppingListView.as_view(), name="shopping"),
    path("shopping/add/", views.ShoppingAddView.as_view(), name="shopping_add"),
    path("shopping/buy-all/", views.ShoppingBuyAllView.as_view(), name="shopping_buy_all"),
    path("shopping/<int:pk>/quantity/", views.ShoppingQuantityView.as_view(), name="shopping_quantity"),
    path("shopping/<int:pk>/buy/", views.ShoppingBuyView.as_view(), name="shopping_buy"),
    path("shopping/<int:pk>/delete/", views.ShoppingDeleteView.as_view(), name="shopping_delete"),
    path("shopping/clear/", views.ShoppingClearView.as_view(), name="shopping_clear"),
]