from django.urls import path

from config.views import ComingSoonView

app_name = "fridge"

urlpatterns = [
    path("", ComingSoonView.as_view(extra_context={"page_title": "Холодильник"}), name="list"),
]