from django import forms
from django.template.defaultfilters import filesizeformat
from django.forms import BaseFormSet, formset_factory

from fridge.models import UNIT_CHOICES, Product

from .models import Recipe

UNIT_LABELS = dict(UNIT_CHOICES)
MAX_PHOTO_SIZE = 5 * 1024 * 1024
MIN_RECIPE_TEXT = 40
MAX_RECIPE_TEXT = 15000


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


class RecipeTextImportForm(forms.Form):
    text = forms.CharField(
        label="Текст рецепта",
        min_length=MIN_RECIPE_TEXT,
        max_length=MAX_RECIPE_TEXT,
        error_messages={
            "required": "Встав текст рецепта: назву, інгредієнти й кроки.",
            "min_length": "Замало тексту. Встав інгредієнти з кількостями й кроки приготування.",
            "max_length": "Текст задовгий. Залиш лише назву, інгредієнти й кроки.",
        },
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 12,
                "placeholder": "Сирники\n\nСир кисломолочний 500 г\nЯйце 1 шт\nБорошно 3 ст. л.\n\nЗмішати сир з яйцем, додати борошно, обсмажити.",
            }
        ),
    )


class ImportedRecipeForm(forms.Form):
    name = forms.CharField(
        label="Назва",
        max_length=150,
        error_messages={"required": "Вкажи назву рецепта."},
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    meal_type = forms.ChoiceField(
        label="Прийом їжі",
        choices=[("", "Не вказано"), *Recipe.MEAL_CHOICES],
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    servings = forms.IntegerField(
        label="Порцій",
        min_value=1,
        max_value=20,
        error_messages={"required": "Вкажи кількість порцій."},
        widget=forms.NumberInput(attrs={"class": "form-control", "inputmode": "numeric"}),
    )
    image = forms.ImageField(
        label="Своє фото",
        required=False,
        widget=forms.ClearableFileInput(attrs={"class": "form-control", "accept": "image/*"}),
    )
    shared = forms.BooleanField(
        label="Спільний рецепт",
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "shared-check"}),
    )

    def __init__(self, *args, allow_shared=False, **kwargs):
        super().__init__(*args, **kwargs)
        if not allow_shared:
            del self.fields["shared"]

    def clean_image(self):
        image = self.cleaned_data.get("image")
        if image and image.size > MAX_PHOTO_SIZE:
            raise forms.ValidationError(f"Фото завелике: максимум {filesizeformat(MAX_PHOTO_SIZE)}.")
        return image


class ImportedIngredientForm(forms.Form):
    original = forms.CharField(required=False, widget=forms.HiddenInput)
    product = forms.ModelChoiceField(
        Product.objects.all(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select js-product"}),
    )
    new_product_name = forms.CharField(required=False, max_length=100, widget=forms.HiddenInput)
    usda_query = forms.CharField(required=False, max_length=255, widget=forms.HiddenInput)
    new_unit = forms.ChoiceField(choices=UNIT_CHOICES, required=False, widget=forms.HiddenInput)
    grams_per_piece = forms.FloatField(required=False, widget=forms.HiddenInput)
    quantity = forms.FloatField(
        required=False,
        min_value=0.01,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "any", "min": "0", "inputmode": "decimal"}),
    )
    optional = forms.BooleanField(required=False)

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.visible_to(user)
        self.fields["product"].empty_label = ""
        self.warning = ""

    def clean(self):
        data = super().clean()
        product = data.get("product")
        if product:
            data["new_product_name"] = ""
        elif not self.is_new_product(data):
            self.add_error("product", "Обери продукт зі списку.")
        if data.get("quantity") is None and not data.get("optional"):
            self.add_error("quantity", "Вкажи кількість або познач «за смаком».")
        return data

    @staticmethod
    def is_new_product(data):
        if not data.get("new_product_name") or not data.get("usda_query"):
            return False
        return data.get("new_unit") != "pcs" or bool(data.get("grams_per_piece"))

    def value_of(self, name):
        return self[name].value() or ""

    def new_product_label(self):
        if self.value_of("product"):
            return ""
        return self.value_of("new_product_name")


class BaseImportedIngredientFormSet(BaseFormSet):
    def kept_forms(self):
        return [
            form for form in self.forms
            if not self._should_delete_form(form) and (form in self.initial_forms or form.has_changed())
        ]

    def clean(self):
        if any(self.errors):
            return
        seen = set()
        kept = self.kept_forms()
        if not kept:
            raise forms.ValidationError("Додай хоча б один інгредієнт.")
        for form in kept:
            product = form.cleaned_data.get("product")
            key = product.pk if product else form.cleaned_data["new_product_name"].casefold()
            if key in seen:
                name = product.name if product else form.cleaned_data["new_product_name"]
                raise forms.ValidationError(f"«{name}» вказано двічі. Залиш один рядок і додай кількості разом.")
            seen.add(key)


ImportedIngredientFormSet = formset_factory(
    ImportedIngredientForm,
    formset=BaseImportedIngredientFormSet,
    extra=0,
    can_delete=True,
)