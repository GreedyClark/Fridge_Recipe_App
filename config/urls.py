from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from accounts.forms import LoginForm
from accounts.views import (
    FamilyCodeView,
    FamilyCreateView,
    FamilyInviteView,
    FamilyJoinView,
    FamilyLeaveView,
    FamilyRemoveMemberView,
    PasswordChangeView,
    ProfileView,
    SignUpView,
)

urlpatterns = [
    path("admin/", admin.site.urls),
    path(
        "login/",
        auth_views.LoginView.as_view(authentication_form=LoginForm, redirect_authenticated_user=True),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("signup/", SignUpView.as_view(), name="signup"),
    path("profile/", ProfileView.as_view(), name="profile"),
    path("profile/password/", PasswordChangeView.as_view(), name="password_change"),
    path("profile/family/create/", FamilyCreateView.as_view(), name="family_create"),
    path("profile/family/join/", FamilyJoinView.as_view(), name="family_join"),
    path("profile/family/leave/", FamilyLeaveView.as_view(), name="family_leave"),
    path("profile/family/code/", FamilyCodeView.as_view(), name="family_code"),
    path("profile/family/remove/<int:user_id>/", FamilyRemoveMemberView.as_view(), name="family_remove"),
    path("family/join/<str:code>/", FamilyInviteView.as_view(), name="family_invite"),
    path("recipes/", include("recipes.urls")),
    path("", include("fridge.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)