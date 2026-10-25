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
    roles = RoleSerializer(source="branch_roles", many=True, required=False)
    password = serializers.CharField(write_only=True, required=False, min_length=8)

    class Meta:
        model = User
        fields = ["id", "username", "first_name", "last_name", "email", "is_active", "last_login", "roles",
                  "password"]
        read_only_fields = ["last_login"]

    def validate_roles(self, roles):
        branches = [r.get("branch") for r in roles]
        if len(branches) != len(set(b.id if b else None for b in branches)):
            raise serializers.ValidationError("One role per branch (and at most one org role)")
        for r in roles:
            try:
                UserBranchRole(branch=r.get("branch"), role=r["role"]).clean()
            except DjangoValidationError as e:
                raise serializers.ValidationError(e.messages)
        return roles

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
        user.branch_roles.all().delete()
        UserBranchRole.objects.bulk_create(
            [UserBranchRole(user=user, branch=r.get("branch"), role=r["role"]) for r in roles]
        )


def me_payload(user, active_branch):
    roles = list(user.branch_roles.select_related("branch"))
    org_role = next((r.role for r in roles if r.branch_id is None), None)
    from tenancy.models import Branch

    if org_role:
        branches = list(Branch.objects.filter(is_active=True))
    else:
        branches = [r.branch for r in roles if r.branch.is_active]
    branch_list = []
    for b in branches:
        branch_role = next((r.role for r in roles if r.branch_id == b.id), None)
        perms = set(permissions_for(org_role)) | set(permissions_for(branch_role))
        branch_list.append({
            "id": b.id, "name": b.name, "code": b.code, "timezone": b.timezone,
            "role": branch_role or org_role, "permissions": sorted(perms),
        })
    return {
        "id": user.id,
        "username": user.username,
        "name": user.get_full_name() or user.username,
        "org_role": org_role,
        "org_permissions": sorted(permissions_for(org_role)) if org_role else [],
        "branches": branch_list,
        "active_branch_id": active_branch.id if active_branch else None,
    }
