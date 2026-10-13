from .models import ShoppingItem


def shopping_count(request):
    if not request.user.is_authenticated:
        return {}
    return {"shopping_count": ShoppingItem.objects.pending().filter(user=request.user).count()}
