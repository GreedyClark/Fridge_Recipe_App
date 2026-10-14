import secrets

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

DEFAULT_DAILY_CALORIES = 2000


class Profile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
        verbose_name="користувач",
    )
    daily_calories = models.PositiveIntegerField(
        "денна норма, ккал",
        default=DEFAULT_DAILY_CALORIES,
        validators=[MinValueValidator(800), MaxValueValidator(6000)],
    )

    class Meta:
        verbose_name = "профіль"
        verbose_name_plural = "профілі"

    def __str__(self):
        return f"{self.user} — {self.daily_calories} ккал"

    @classmethod
    def for_user(cls, user):
        profile, _ = cls.objects.get_or_create(user=user)
        return profile


INVITE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
INVITE_LENGTH = 8
MAX_MEMBERS = 6


def new_invite_code():
    return "".join(secrets.choice(INVITE_ALPHABET) for _ in range(INVITE_LENGTH))


class Household(models.Model):
    name = models.CharField("назва", max_length=60)
    invite_code = models.CharField("код запрошення", max_length=INVITE_LENGTH, unique=True, default=new_invite_code)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="+",
        verbose_name="створив",
    )
    created_at = models.DateTimeField("створено", auto_now_add=True)

    class Meta:
        verbose_name = "сім'я"
        verbose_name_plural = "сім'ї"

    def __str__(self):
        return self.name

    @property
    def is_full(self):
        return self.memberships.count() >= MAX_MEMBERS

    def refresh_code(self):
        self.invite_code = new_invite_code()
        self.save(update_fields=["invite_code"])


class Membership(models.Model):
    OWNER = "owner"
    MEMBER = "member"
    ROLE_CHOICES = [(OWNER, "створив сім'ю"), (MEMBER, "учасник")]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="membership",
        verbose_name="користувач",
    )
    household = models.ForeignKey(
        Household,
        on_delete=models.CASCADE,
        related_name="memberships",
        verbose_name="сім'я",
    )
    role = models.CharField("роль", max_length=10, choices=ROLE_CHOICES, default=MEMBER)
    joined_at = models.DateTimeField("приєднався", auto_now_add=True)

    class Meta:
        ordering = ["joined_at"]
        verbose_name = "учасник сім'ї"
        verbose_name_plural = "учасники сім'ї"

    def __str__(self):
        return f"{self.user} у «{self.household}»"

    @property
    def is_owner(self):
        return self.role == self.OWNER


def household_of(user):
    membership = Membership.objects.filter(user=user).select_related("household").first()
    return membership.household if membership else None


def people_ids(user):
    if user is None or not user.pk:
        return []
    household_id = Membership.objects.filter(user=user).values_list("household_id", flat=True).first()
    if household_id is None:
        return [user.pk]
    return list(Membership.objects.filter(household_id=household_id).values_list("user_id", flat=True))
