from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView


class ComingSoonView(LoginRequiredMixin, TemplateView):
    template_name = "placeholder.html"