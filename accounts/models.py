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
