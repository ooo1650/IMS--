"""Deployment-wide settings stored in the database (admin_extras.SystemSettings)."""


def system_settings():
    from .models import SystemSettings

    obj, _ = SystemSettings.objects.get_or_create(pk=1)
    return obj
