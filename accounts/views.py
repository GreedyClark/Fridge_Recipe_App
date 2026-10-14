from django.contrib import messages
from django.contrib.auth import login, update_session_auth_hash
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import CreateView

from .family import FamilyError, create_household, join_household, leave_household
from .forms import DailyCaloriesForm, HouseholdForm, JoinForm, SignUpForm, StyledPasswordChangeForm
from .models import MAX_MEMBERS, Household, Membership, Profile


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

    def render_page(self, password_form=None, household_form=None, join_form=None):
        user = self.request.user
        membership = Membership.objects.filter(user=user).select_related("household").first()
        context = {
            "norm_form": DailyCaloriesForm(instance=Profile.for_user(user)),
            "password_form": password_form or StyledPasswordChangeForm(user),
            "password_open": password_form is not None,
            "membership": membership,
            "household_form": household_form or HouseholdForm(),
            "join_form": join_form or JoinForm(),
            "max_members": MAX_MEMBERS,
        }
        if membership:
            household = membership.household
            context["members"] = household.memberships.select_related("user")
            context["invite_url"] = self.request.build_absolute_uri(
                reverse("family_invite", args=[household.invite_code])
            )
        return render(self.request, self.template_name, context)

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


class FamilyCreateView(ProfileView):
    def post(self, request):
        form = HouseholdForm(request.POST)
        if not form.is_valid():
            return self.render_page(household_form=form)
        try:
            household = create_household(request.user, form.cleaned_data["name"])
        except FamilyError as exc:
            messages.error(request, str(exc))
            return redirect("profile")
        messages.success(request, f"Сім'ю «{household.name}» створено. Надішли код тим, з ким живеш.")
        return redirect("profile")


def join_and_report(request, household):
    try:
        join_household(request.user, household)
    except FamilyError as exc:
        messages.error(request, str(exc))
        return False
    messages.success(request, f"Ти в сім'ї «{household.name}». Тепер холодильник, покупки й рецепти спільні.")
    return True


class FamilyJoinView(ProfileView):
    def post(self, request):
        form = JoinForm(request.POST)
        if not form.is_valid():
            return self.render_page(join_form=form)
        join_and_report(request, form.household)
        return redirect("profile")


class FamilyInviteView(LoginRequiredMixin, View):
    template_name = "accounts/family_invite.html"

    def get_household(self, code):
        return get_object_or_404(Household, invite_code=code.upper())

    def get(self, request, code):
        household = self.get_household(code)
        context = {
            "household": household,
            "members": household.memberships.select_related("user"),
            "membership": Membership.objects.filter(user=request.user).select_related("household").first(),
            "is_full": household.is_full,
        }
        return render(request, self.template_name, context)

    def post(self, request, code):
        if join_and_report(request, self.get_household(code)):
            return redirect("fridge:list")
        return redirect("family_invite", code=code)


class FamilyLeaveView(LoginRequiredMixin, View):
    def post(self, request):
        membership = Membership.objects.filter(user=request.user).select_related("household").first()
        if membership:
            leave_household(request.user)
            messages.success(request, f"Ти більше не в сім'ї «{membership.household.name}».")
        return redirect("profile")


class OwnerRequiredMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            self.membership = get_object_or_404(
                Membership.objects.select_related("household"), user=request.user, role=Membership.OWNER
            )
        return super().dispatch(request, *args, **kwargs)


class FamilyRemoveMemberView(OwnerRequiredMixin, View):
    def post(self, request, user_id):
        member = get_object_or_404(
            Membership.objects.select_related("user"), household=self.membership.household, user_id=user_id
        )
        if member.user_id != request.user.pk:
            leave_household(member.user)
            messages.success(request, f"{member.user.username} більше не в сім'ї.")
        return redirect("profile")


class FamilyCodeView(OwnerRequiredMixin, View):
    def post(self, request):
        self.membership.household.refresh_code()
        messages.success(request, "Код оновлено. Старе посилання більше не працює.")
        return redirect("profile")
