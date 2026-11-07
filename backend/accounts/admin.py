from django import forms
from django.contrib import admin

from audit.models import AuditEntry
from audit.records import record_operator_event

from .models import Membership, Tenant, User

Action = AuditEntry.Action


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

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        # An operator who sets a password can sign in as that User, so every Tenant the User belongs to is told.
        if change and form.cleaned_data.get("password"):
            for membership in obj.memberships.select_related("tenant"):
                record_operator_event(membership.tenant, Action.PASSWORD_RESET, obj.email, request.user)

    # Deactivate instead: deleting would also remove the user's Memberships.
    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "tenant", "role")
    list_filter = ("role", "tenant")
    search_fields = ("user__email", "tenant__name")
    autocomplete_fields = ("user", "tenant")

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        action = Action.MEMBER_CHANGED if change else Action.MEMBER_ADDED
        record_operator_event(obj.tenant, action, f"{obj.user.email} as {obj.role}", request.user)

    def delete_model(self, request, obj):
        record_operator_event(obj.tenant, Action.MEMBER_REMOVED, obj.user.email, request.user)
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):  # the "delete selected" action
        for membership in queryset.select_related("tenant", "user"):
            record_operator_event(membership.tenant, Action.MEMBER_REMOVED, membership.user.email, request.user)
        super().delete_queryset(request, queryset)
