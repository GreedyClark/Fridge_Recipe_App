from django import forms

from .models import FridgeItem


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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].empty_label = "Оберіть продукт"