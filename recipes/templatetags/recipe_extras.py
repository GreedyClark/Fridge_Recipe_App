from django import template

register = template.Library()


@register.filter
def uk_plural(value, forms):
    one, few, many = forms.split(",")
    number = abs(int(value))
    if number % 10 == 1 and number % 100 != 11:
        return one
    if 2 <= number % 10 <= 4 and not 12 <= number % 100 <= 14:
        return few
    return many