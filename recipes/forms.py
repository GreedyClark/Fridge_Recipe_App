from django import forms


class RecipeImportForm(forms.Form):
    url = forms.URLField(
        label="Посилання на рецепт",
        max_length=500,
        assume_scheme="https",
        error_messages={
            "required": "Встав посилання на сторінку з рецептом.",
            "invalid": "Це не схоже на посилання.",
        },
        widget=forms.URLInput(
            attrs={"class": "form-control", "placeholder": "https://…", "autocomplete": "off", "inputmode": "url"}
        ),
    )