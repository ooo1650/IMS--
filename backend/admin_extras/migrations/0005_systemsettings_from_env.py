"""Copy the settings that used to come from environment variables (SMTP,
roles needing a sign-in code, lockout limits) into SystemSettings, so moving
them to the console changes nothing for a running install."""
import os

from django.db import migrations


def forwards(apps, schema_editor):
    SystemSettings = apps.get_model("admin_extras", "SystemSettings")
    if SystemSettings.objects.exists():
        return
    env = os.environ.get
    obj = SystemSettings(pk=1)
    obj.smtp_host = env("EMAIL_HOST", "")
    try:
        obj.smtp_port = int(env("EMAIL_PORT", "587"))
    except ValueError:
        pass
    obj.smtp_user = env("EMAIL_HOST_USER", "")
    obj.smtp_use_tls = env("EMAIL_USE_TLS", "true").lower() in ("1", "true", "yes")
    obj.from_email = env("DEFAULT_FROM_EMAIL", "")
    if env("EMAIL_HOST_PASSWORD"):
        from compliance import crypto

        obj.smtp_password_enc = crypto.encrypt(env("EMAIL_HOST_PASSWORD"))
    roles = [r.strip() for r in env("IMS_2FA_REQUIRED_ROLES", "SuperAdmin,OrgAdmin,BranchManager").split(",") if r.strip()]
    obj.otp_required_roles = roles
    for field, name in (("login_window_minutes", "IMS_LOGIN_WINDOW_MINUTES"), ("login_max_per_username", "IMS_LOGIN_MAX_PER_USERNAME"),
                        ("login_max_per_ip", "IMS_LOGIN_MAX_PER_IP")):
        try:
            if env(name):
                setattr(obj, field, int(env(name)))
        except ValueError:
            pass
    obj.save()


class Migration(migrations.Migration):
    dependencies = [("admin_extras", "0004_systemsettings")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
