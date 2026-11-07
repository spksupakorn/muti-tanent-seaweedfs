import uuid

from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.db import models


class Tenant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class UserManager(BaseUserManager):
    def create_user(self, email, password):
        user = self.model(email=email.strip().lower())
        user.set_password(password)
        user.save(using=self._db)
        return user


class User(AbstractBaseUser):
    """A person who signs in. Global: belongs to Tenants only through Membership."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True)
    is_active = models.BooleanField(default=True)
    # A Platform operator may sign in to the back office (Django admin) and, there, do everything it offers.
    is_operator = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    USERNAME_FIELD = "email"
    objects = UserManager()

    def __str__(self):
        return self.email

    # The names Django admin looks for. No fine-grained permissions: an operator can use every admin page.
    @property
    def is_staff(self) -> bool:
        return self.is_active and self.is_operator

    @property
    def is_superuser(self) -> bool:
        return self.is_staff

    def has_perm(self, perm, obj=None) -> bool:
        return self.is_staff

    def has_module_perms(self, app_label) -> bool:
        return self.is_staff


class Membership(models.Model):
    class Role(models.TextChoices):
        ADMIN = "admin"
        MEMBER = "member"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships")
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=10, choices=Role.choices)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "tenant"], name="one_membership_per_user_tenant")]
