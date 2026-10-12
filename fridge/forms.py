from django import forms

from .models import UNIT_CHOICES, FridgeItem, Product


class FridgeItemForm(forms.ModelForm):
    class Meta:
        model = FridgeItem
        fields = ("product", "quantity", "expiry_date")
        widgets = {
            "product": forms.Select(attrs={"class": "form-select"}),
            "quantity": forms.NumberInput(
                attrs={"class": "form-control", "step": "any", "min": "0", "inputmode": "decimal"}
            ),
            "expiry_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}, format="%Y-%m-%d"),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.visible_to(user)
        self.fields["product"].empty_label = "Оберіть продукт"


class ProductCreateForm(forms.Form):
    name = forms.CharField(
        label="Назва українською",
        max_length=100,
        error_messages={"required": "Вкажи назву продукту."},
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "Наприклад, Сир бринза", "autocomplete": "off"}
        ),
    )
    unit = forms.ChoiceField(label="Одиниця", choices=UNIT_CHOICES, initial="g", widget=forms.RadioSelect)
    grams_per_piece = forms.FloatField(
        label="Вага 1 шт",
        required=False,
        min_value=1,
        max_value=5000,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "any", "inputmode": "decimal"}),
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean_name(self):
        name = " ".join(self.cleaned_data["name"].split())
        name = name[:1].upper() + name[1:]
        for product in Product.objects.visible_to(self.user):
            if product.name.casefold() == name.casefold():
                raise forms.ValidationError(f"«{product.name}» уже є в довіднику. Обери його у формі холодильника.")
        return name

    def clean(self):
        data = super().clean()
        if data.get("unit") == "pcs" and not data.get("grams_per_piece"):
            self.add_error("grams_per_piece", "Вкажи вагу однієї штуки в грамах.")
        if data.get("unit") != "pcs":
            data["grams_per_piece"] = None
        return data