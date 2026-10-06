from django.contrib.auth.forms import AuthenticationForm, UserCreationForm


class StyledFormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")


class LoginForm(StyledFormMixin, AuthenticationForm):
    pass


class SignUpForm(StyledFormMixin, UserCreationForm):
    pass