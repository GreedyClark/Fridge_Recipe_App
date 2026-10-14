from django import forms
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm, UserCreationForm

from .models import INVITE_LENGTH, Household, Profile


class StyledFormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")


class LoginForm(StyledFormMixin, AuthenticationForm):
    pass


class SignUpForm(StyledFormMixin, UserCreationForm):
    pass


class StyledPasswordChangeForm(StyledFormMixin, PasswordChangeForm):
    pass


class DailyCaloriesForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ["daily_calories"]
        widgets = {
            "daily_calories": forms.NumberInput(
                attrs={"class": "norm-input", "step": "50", "min": "800", "max": "6000", "inputmode": "numeric"}
            ),
        }
        error_messages = {
            "daily_calories": {
                "min_value": "Норма має бути не менше 800 ккал.",
                "max_value": "Норма має бути не більше 6000 ккал.",
                "required": "Вкажи денну норму.",
            },
        }


class HouseholdForm(forms.ModelForm):
    class Meta:
        model = Household
        fields = ["name"]
        labels = {"name": "Назва"}
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "Наприклад, Дім на Липовій"}),
        }
        error_messages = {"name": {"required": "Придумай назву для сім'ї."}}


class JoinForm(forms.Form):
    code = forms.CharField(
        label="Код запрошення",
        max_length=INVITE_LENGTH + 4,
        error_messages={"required": "Встав код, який тобі надіслали."},
        widget=forms.TextInput(
            attrs={"class": "form-control invite-input", "placeholder": "Наприклад, K7M2QX9P", "autocomplete": "off"}
        ),
    )

    def clean_code(self):
        code = "".join(self.cleaned_data["code"].split()).upper()
        household = Household.objects.filter(invite_code=code).first()
        if household is None:
            raise forms.ValidationError("Такого коду немає. Перевір його або попроси новий.")
        self.household = household
        return code
