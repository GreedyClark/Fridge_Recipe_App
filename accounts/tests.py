from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import Profile


class ProfileTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("cook", password="Old-pass-123")
        self.client.force_login(self.user)

    def test_header_links_to_profile(self):
        response = self.client.get(reverse("fridge:list"))

        self.assertContains(response, f'href="{reverse("profile")}"')
        self.assertNotContains(response, f'action="{reverse("logout")}"')

    def test_profile_page(self):
        response = self.client.get(reverse("profile"))

        self.assertContains(response, "cook")
        self.assertContains(response, "Вийти з акаунту")
        self.assertContains(response, f'action="{reverse("logout")}"')
        self.assertContains(response, 'value="2000"')

    def test_profile_requires_login(self):
        self.client.logout()

        response = self.client.get(reverse("profile"))

        self.assertEqual(response.status_code, 302)

    def test_logout_from_profile(self):
        self.client.post(reverse("logout"))

        self.assertEqual(self.client.get(reverse("fridge:list")).status_code, 302)

    def test_norm_from_profile_returns_to_profile(self):
        response = self.client.post(reverse("recipes:daily_calories"), {"daily_calories": 1700, "next": "profile"})

        self.assertRedirects(response, reverse("profile"))
        self.assertEqual(Profile.for_user(self.user).daily_calories, 1700)

    def test_change_password(self):
        data = {"old_password": "Old-pass-123", "new_password1": "New-pass-456!", "new_password2": "New-pass-456!"}

        response = self.client.post(reverse("password_change"), data, follow=True)

        self.assertContains(response, "Пароль змінено")
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("New-pass-456!"))
        self.assertEqual(self.client.get(reverse("fridge:list")).status_code, 200)

    def test_wrong_old_password(self):
        data = {"old_password": "wrong", "new_password1": "New-pass-456!", "new_password2": "New-pass-456!"}

        response = self.client.post(reverse("password_change"), data)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "field-error")
        self.assertContains(response, "<details class=\"password-box\" open>")
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Old-pass-123"))


class ForbiddenPageTests(TestCase):
    def test_custom_403(self):
        self.client.force_login(User.objects.create_user("cook", password="pass"))

        response = self.client.get(reverse("recipes:drafts"))

        self.assertContains(response, "Немає доступу", status_code=403)
