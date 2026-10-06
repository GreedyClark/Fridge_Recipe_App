from django.urls import path

from config.views import ComingSoonView

app_name = "recipes"

urlpatterns = [
    path("", ComingSoonView.as_view(extra_context={"page_title": "Рецепти"}), name="list"),
    path("what-can-i-cook/", ComingSoonView.as_view(extra_context={"page_title": "Що приготувати?"}), name="what_can_i_cook"),
    path("log/", ComingSoonView.as_view(extra_context={"page_title": "Журнал"}), name="log"),
]