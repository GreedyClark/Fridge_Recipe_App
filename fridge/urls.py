from django.urls import path

from . import views

app_name = "fridge"

urlpatterns = [
    path("", views.FridgeListView.as_view(), name="list"),
    path("add/", views.FridgeCreateView.as_view(), name="add"),
    path("<int:pk>/edit/", views.FridgeUpdateView.as_view(), name="edit"),
    path("<int:pk>/delete/", views.FridgeDeleteView.as_view(), name="delete"),
]