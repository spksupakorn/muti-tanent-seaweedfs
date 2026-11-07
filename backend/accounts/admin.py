from django import forms
from django.contrib import admin

from .models import Membership, Tenant, User


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("name", "created_at")
    search_fields = ("name",)

    # Deleting a Tenant would orphan its stored files. End access through Memberships instead.
    def has_delete_permission(self, request, obj=None):
        return False


class UserForm(forms.ModelForm):
    password = forms.CharField(
        widget=forms.PasswordInput(render_value=False),
        required=False,
        help_text="Required for a new user. Leave empty to keep the current password.",
    )

    class Meta:
        model = User
        fields = ("email", "password", "is_active", "is_operator")

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()

    def clean(self):
        data = super().clean()
        # `pk` is already set on a new User (its id has a default), so ask whether the form is adding.
        if self.instance._state.adding and not data.get("password"):
            self.add_error("password", "A new user needs a password.")
        return data

    def save(self, commit=True):
        user = super().save(commit=False)
        if self.cleaned_data.get("password"):
            user.set_password(self.cleaned_data["password"])
        if commit:
            user.save()
        return user


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    form = UserForm
    list_display = ("email", "is_active", "is_operator", "created_at")
    list_filter = ("is_active", "is_operator")
    search_fields = ("email",)

    # Deactivate instead: deleting would also remove the user's Memberships.
    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "tenant", "role")
    list_filter = ("role", "tenant")
    search_fields = ("user__email", "tenant__name")
    autocomplete_fields = ("user", "tenant")
