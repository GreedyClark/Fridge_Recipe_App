from django.db import transaction

from fridge.models import FridgeItem, Product, ShoppingItem
from recipes.models import RecipeIngredient

from .models import MAX_MEMBERS, Household, Membership


class FamilyError(Exception):
    pass


def product_usages(product, user_ids):
    return [
        FridgeItem.objects.filter(product=product, user__in=user_ids),
        ShoppingItem.objects.filter(product=product, user__in=user_ids),
        RecipeIngredient.objects.filter(product=product, recipe__owner__in=user_ids),
    ]


def move_usages(source, target, user_ids):
    for usages in product_usages(source, user_ids):
        usages.update(product=target)


def merge_shopping(product, user_ids):
    items = list(ShoppingItem.objects.pending().filter(product=product, user__in=user_ids).order_by("created_at"))
    if len(items) < 2:
        return
    first, *rest = items
    first.quantity = round(first.quantity + sum(item.quantity for item in rest), 2)
    first.save(update_fields=["quantity"])
    ShoppingItem.objects.filter(pk__in=[item.pk for item in rest]).delete()


def merge_duplicate_products(user, household):
    others = list(household.memberships.exclude(user=user).values_list("user_id", flat=True))
    family = {product.name.casefold(): product for product in Product.objects.filter(owner__in=others)}
    everyone = [user.pk, *others]
    for product in Product.objects.filter(owner=user):
        twin = family.get(product.name.casefold())
        if twin is None:
            continue
        move_usages(product, twin, everyone)
        merge_shopping(twin, everyone)
        product.delete()


def copy_for(product, owner):
    existing = Product.objects.filter(owner=owner, name=product.name).first()
    if existing:
        return existing
    copy = Product.objects.get(pk=product.pk)
    copy.pk = None
    copy.owner = owner
    copy.save()
    return copy


def split_products(leaver, remaining):
    heir = remaining[0]
    remaining_ids = [member.pk for member in remaining]
    for product in Product.objects.filter(owner=leaver):
        if any(usages.exists() for usages in product_usages(product, remaining_ids)):
            move_usages(product, copy_for(product, heir), remaining_ids)
    for product in Product.objects.filter(owner__in=remaining_ids):
        if any(usages.exists() for usages in product_usages(product, [leaver.pk])):
            move_usages(product, copy_for(product, leaver), [leaver.pk])

    ShoppingItem.objects.filter(user__in=remaining_ids, taken_by=leaver).update(taken_by=None)
    ShoppingItem.objects.filter(user=leaver).exclude(taken_by=leaver).update(taken_by=None)


@transaction.atomic
def create_household(user, name):
    if Membership.objects.filter(user=user).exists():
        raise FamilyError("Ти вже в сім'ї. Спершу вийди з неї.")
    household = Household.objects.create(name=name, created_by=user)
    Membership.objects.create(user=user, household=household, role=Membership.OWNER)
    return household


@transaction.atomic
def join_household(user, household):
    household = Household.objects.select_for_update().get(pk=household.pk)
    if Membership.objects.filter(user=user).exists():
        raise FamilyError("Ти вже в сім'ї. Щоб приєднатися до іншої, спершу вийди з поточної.")
    if household.memberships.count() >= MAX_MEMBERS:
        raise FamilyError(f"У сім'ї «{household.name}» вже {MAX_MEMBERS} людей, більше додати не можна.")
    Membership.objects.create(user=user, household=household)
    merge_duplicate_products(user, household)
    return household


@transaction.atomic
def leave_household(user):
    membership = Membership.objects.select_related("household").filter(user=user).first()
    if membership is None:
        return
    household = membership.household
    remaining = [item.user for item in household.memberships.exclude(user=user).select_related("user")]
    membership.delete()
    if not remaining:
        household.delete()
        return
    split_products(user, remaining)
    if membership.is_owner:
        Membership.objects.filter(user=remaining[0]).update(role=Membership.OWNER)
