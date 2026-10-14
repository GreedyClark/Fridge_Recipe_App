from .models import ShoppingItem


def shopping_count(request):
    if not request.user.is_authenticated:
        return {}
    return {"shopping_count": ShoppingItem.objects.pending().for_people(request.user).count()}
