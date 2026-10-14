from django import forms
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm, UserCreationForm

from .models import Profile


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
