from django.urls import path

from . import views

app_name = "recipes"

urlpatterns = [
    path("", views.RecipeListView.as_view(), name="list"),
    path("<int:pk>/", views.RecipeDetailView.as_view(), name="detail"),
    path("<int:pk>/cook/", views.CookRecipeView.as_view(), name="cook"),
    path("what-can-i-cook/", views.WhatCanICookView.as_view(), name="what_can_i_cook"),
    path("log/", views.CookingLogListView.as_view(), name="log"),
]