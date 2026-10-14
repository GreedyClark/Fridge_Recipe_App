from django.contrib import messages
from django.contrib.auth import login, update_session_auth_hash
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView

from .forms import DailyCaloriesForm, SignUpForm, StyledPasswordChangeForm
from .models import Profile


class SignUpView(CreateView):
    form_class = SignUpForm
    template_name = "accounts/signup.html"
    success_url = reverse_lazy("fridge:list")

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect(self.success_url)
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        login(self.request, self.object)
        return response


class ProfileView(LoginRequiredMixin, View):
    template_name = "accounts/profile.html"

    def render_page(self, password_form=None, status=200):
        context = {
            "norm_form": DailyCaloriesForm(instance=Profile.for_user(self.request.user)),
            "password_form": password_form or StyledPasswordChangeForm(self.request.user),
            "password_open": password_form is not None,
        }
        return render(self.request, self.template_name, context, status=status)

    def get(self, request):
        return self.render_page()


class PasswordChangeView(ProfileView):
    def post(self, request):
        form = StyledPasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)
            messages.success(request, "Пароль змінено.")
            return redirect("profile")
        return self.render_page(password_form=form)
