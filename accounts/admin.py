from django.contrib import admin

from .models import Household, Membership, Profile


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "daily_calories")


class MembershipInline(admin.TabularInline):
    model = Membership
    extra = 0


@admin.register(Household)
class HouseholdAdmin(admin.ModelAdmin):
    list_display = ("name", "invite_code", "created_by", "created_at")
    inlines = [MembershipInline]
