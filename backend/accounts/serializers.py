from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import serializers

from .models import User, UserBranchRole
from .roles import ROLE_CHOICES, permissions_for


class RoleSerializer(serializers.ModelSerializer):
    branch_name = serializers.CharField(source="branch.name", read_only=True, default=None)
    role = serializers.ChoiceField(choices=ROLE_CHOICES)

    class Meta:
        model = UserBranchRole
        fields = ["id", "branch", "branch_name", "role"]


class UserSerializer(serializers.ModelSerializer):
    """A user as seen from one business (``context["org"]``): only their roles in
    that business are shown and replaced. Someone who also works for another
    business can't have their sign-in details (username, email, password,
    active) changed from here; the operator console does that."""

    roles = RoleSerializer(source="branch_roles", many=True, required=False)
    password = serializers.CharField(write_only=True, required=False)
    otp_required = serializers.SerializerMethodField()
    other_businesses = serializers.SerializerMethodField()

    IDENTITY_FIELDS = ("username", "email", "password", "is_active")

    class Meta:
        model = User
        fields = ["id", "username", "first_name", "last_name", "email", "email_verified", "otp_enabled",
                  "otp_required", "is_active", "last_login", "roles", "password", "other_businesses"]
        read_only_fields = ["last_login", "email_verified"]

    def _org(self):
        return self.context.get("org")

    def get_otp_required(self, user):
        return user.otp_required()

    def get_other_businesses(self, user):
        """How many other businesses this person also works for (identity fields locked when > 0)."""
        org = self._org()
        return len(user.org_ids() - ({org.pk} if org else set()))

    def to_representation(self, user):
        data = super().to_representation(user)
        org = self._org()
        if org is not None:
            data["roles"] = [r for r, role in zip(data["roles"], user.branch_roles.all()) if role.org_id == org.pk]
        return data

    def validate_email(self, email):
        email = email.strip().lower()
        clash = User.objects.filter(email__iexact=email)
        if self.instance:
            clash = clash.exclude(pk=self.instance.pk)
        if email and clash.exists():
            raise serializers.ValidationError("Another user already has this email")
        return email

    def validate_password(self, password):
        try:
            validate_password(password, self.instance)
        except DjangoValidationError as e:
            raise serializers.ValidationError(e.messages)
        return password

    def validate_roles(self, roles):
        org = self._org()
        branches = [r.get("branch") for r in roles]
        if len(branches) != len(set(b.id if b else None for b in branches)):
            raise serializers.ValidationError("One role per branch (and at most one business-wide role)")
        for r in roles:
            if org is not None and r.get("branch") is not None and r["branch"].org_id != org.pk:
                raise serializers.ValidationError("That branch belongs to another business")
            try:
                UserBranchRole(branch=r.get("branch"), role=r["role"]).clean()
            except DjangoValidationError as e:
                raise serializers.ValidationError(e.messages)
        return roles

    def validate(self, data):
        user, org = self.instance, self._org()
        if user is not None and org is not None and user.org_ids() - {org.pk}:
            changed = [f for f in self.IDENTITY_FIELDS
                       if f in data and (f == "password" or data[f] != getattr(user, f))]
            if changed:
                raise serializers.ValidationError(
                    {"detail": "This person also works for another business, so their sign-in details "
                               "(username, email, password, active) can only be changed by the operator."})
        return data

    @transaction.atomic
    def create(self, data):
        roles = data.pop("branch_roles", [])
        password = data.pop("password", None)
        if not password:
            raise serializers.ValidationError({"password": "Required for new users"})
        user = User(**data)
        user.set_password(password)
        user.save()
        self._set_roles(user, roles)
        return user

    @transaction.atomic
    def update(self, user, data):
        roles = data.pop("branch_roles", None)
        password = data.pop("password", None)
        if "email" in data and data["email"] != user.email:
            user.email_verified = False  # proven again by the next code sent there
        for k, v in data.items():
            setattr(user, k, v)
        if password:
            user.set_password(password)
        user.save()
        if roles is not None:
            self._set_roles(user, roles)
        # Deactivation revokes instantly: ModelBackend.get_user() rejects inactive
        # users on their next request, so existing sessions stop working.
        return user

    def _set_roles(self, user, roles):
        """Replace the user's roles in this business only; other businesses keep theirs."""
        org = self._org()
        if org is None:
            raise serializers.ValidationError({"roles": "Choose a business first"})
        user.branch_roles.filter(org=org).delete()
        for r in roles:  # save(), not bulk_create: it fills org from the branch
            UserBranchRole(user=user, org=org, branch=r.get("branch"), role=r["role"]).save()


def me_payload(user, active_branch, active_org=None):
    """The signed-in user, their businesses and the branches they can open.
    ``branches`` and ``org_*`` describe the active business only; ``businesses``
    lists every business they work for (for the switcher)."""
    from tenancy.models import Branch, Organization, branch_colors

    roles = list(user.branch_roles.select_related("branch", "org"))
    org = active_branch.org if active_branch is not None else active_org
    org_ids = sorted({r.org_id for r in roles})
    if org is None and len(org_ids) == 1:
        org = Organization.objects.filter(pk=org_ids[0]).first()
    businesses = []
    for o in Organization.objects.filter(pk__in=org_ids, is_active=True).order_by("name"):
        org_wide = next((r.role for r in roles if r.org_id == o.pk and r.branch_id is None), None)
        businesses.append({"id": o.pk, "name": o.name, "code": o.code or "", "role": org_wide})
    org_role = next((r.role for r in roles if org and r.org_id == org.pk and r.branch_id is None), None)
    if org is None:
        branches = []
    elif org_role:
        branches = list(Branch.objects.filter(org=org, is_active=True))
    else:
        branches = [r.branch for r in roles if r.branch_id and r.org_id == org.pk and r.branch.is_active]
    branch_list = []
    colors = branch_colors(org.pk) if org is not None else {}
    for b in branches:
        branch_role = next((r.role for r in roles if r.branch_id == b.id), None)
        perms = set(permissions_for(org_role)) | set(permissions_for(branch_role))
        branch_list.append({
            "id": b.id, "name": b.name, "code": b.code, "timezone": b.timezone, "color": colors.get(b.id, "grey"),
            "role": branch_role or org_role, "permissions": sorted(perms),
        })
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "email_verified": user.email_verified,
        "otp_enabled": user.otp_enabled,
        "otp_required": user.otp_required(),
        "name": user.get_full_name() or user.username,
        "is_platform_admin": user.is_platform_admin,
        "businesses": businesses,
        "active_org_id": org.pk if org else None,
        "active_org_name": org.name if org else None,
        "org_role": org_role,
        "org_permissions": sorted(permissions_for(org_role)) if org_role else [],
        "branches": branch_list,
        "active_branch_id": active_branch.id if active_branch else None,
        # Testing phase: the app shows a warning banner while one-click sign-in is on.
        "test_login": settings.IMS_TEST_LOGIN,
    }
