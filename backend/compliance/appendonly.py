from django.core.exceptions import ValidationError
from django.db import models


class AppendOnlyError(ValidationError):
    pass


class AppendOnlyModel(models.Model):
    """Issued tax documents are append-only (payment.md Step 2): no app, API or
    ORM path may change or delete them. Postgres triggers (compliance
    migration 0002) enforce the same rules at the database level."""

    # Fields that may still change after issue (e.g. cancellation status).
    MUTABLE_AFTER_ISSUE: tuple = ()

    class Meta:
        abstract = True

    def is_issued(self):
        return True

    def save(self, *args, **kwargs):
        if self.pk and not self._state.adding:
            old = type(self).objects.filter(pk=self.pk).first()
            if old is not None and old.is_issued():
                update_fields = kwargs.get("update_fields")
                fields = [f for f in self._meta.concrete_fields if not f.primary_key]
                if update_fields is not None:
                    fields = [f for f in fields if f.name in update_fields or f.attname in update_fields]
                changed = [f.name for f in fields
                           if getattr(old, f.attname) != getattr(self, f.attname)
                           and f.name not in self.MUTABLE_AFTER_ISSUE]
                if changed:
                    raise AppendOnlyError(f"{type(self).__name__} {self.pk} is issued; cannot change {changed}")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AppendOnlyError(f"{type(self).__name__} records are append-only and cannot be deleted")
