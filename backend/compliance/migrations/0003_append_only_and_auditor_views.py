"""Database-level guards and auditor views (payment.md Step 2).

PostgreSQL (production):
  * issued tax documents, their lines, payments, print and audit logs can't be
    DELETEd; credit/debit notes, purchase documents, print and audit logs
    can't be UPDATEd; an issued sale may only change its cancellation and
    review columns. Enforced by triggers, so it holds for raw SQL too.
  * cbms_bill_view / cbms_return_view: MATERIALIZED views of the CBMS fields
    for IRD auditors; refreshed by ``manage.py refresh_auditor_views`` (cron).
SQLite (dev only): plain views; append-only is enforced by the models.
"""
from django.db import migrations

DELETE_PROTECTED = [
    "sales_sale", "sales_saleline", "sales_payment", "sales_receipt", "sales_creditnote", "sales_creditnoteline",
    "sales_debitnote", "sales_printlog", "admin_extras_auditlog", "purchasing_goodsreceipt",
    "purchasing_goodsreceiptline", "purchasing_purchasereturn", "purchasing_purchasereturnline",
]
UPDATE_PROTECTED = [
    "sales_creditnote", "sales_creditnoteline", "sales_debitnote", "sales_printlog", "admin_extras_auditlog",
    "purchasing_goodsreceipt", "purchasing_goodsreceiptline", "purchasing_purchasereturn",
    "purchasing_purchasereturnline",
]
SALE_MUTABLE = ["status", "voided_at", "voided_by_id", "void_reason", "conflict_resolution", "conflict_resolved_by_id",
                "conflict_resolved_at", "needs_review", "stock_conflict"]

BILL_VIEW_SELECT = """
SELECT s.id AS sale_id, b.code AS branch_code, o.pan AS seller_pan, s.buyer_pan, s.buyer_name,
       s.fiscal_year, s.receipt_number AS invoice_number, s.issued_bs AS invoice_date, s.issued_at,
       s.transaction_bs, s.invoice_type, s.payment_method, s.status,
       s.total_paisa / 100.0 AS total_sales, s.taxable_sales_vat_paisa / 100.0 AS taxable_sales_vat,
       s.tax_total_paisa / 100.0 AS vat, s.excisable_amount_paisa / 100.0 AS excisable_amount,
       s.excise_total_paisa / 100.0 AS excise, s.taxable_sales_hst_paisa / 100.0 AS taxable_sales_hst,
       s.hst_total_paisa / 100.0 AS hst, s.amount_for_esf_paisa / 100.0 AS amount_for_esf,
       s.esf_total_paisa / 100.0 AS esf, s.zero_rated_sales_paisa / 100.0 AS export_sales,
       s.tax_exempted_sales_paisa / 100.0 AS tax_exempted_sales,
       p.status AS cbms_status, p.last_code AS cbms_last_code, p.synced_at AS cbms_synced_at
FROM sales_sale s
JOIN tenancy_branch b ON b.id = s.branch_id
JOIN tenancy_organization o ON o.id = b.org_id
LEFT JOIN compliance_cbmspost p ON p.key = 'bill:' || s.id
WHERE s.receipt_number <> ''
"""
RETURN_VIEW_SELECT = """
SELECT c.id AS credit_note_id, b.code AS branch_code, o.pan AS seller_pan, s.buyer_pan, s.buyer_name,
       c.fiscal_year, s.receipt_number AS ref_invoice_number, c.number AS credit_note_number,
       c.issued_bs AS credit_note_date, c.reason AS reason_for_return,
       c.total_paisa / 100.0 AS total_sales, c.taxable_sales_vat_paisa / 100.0 AS taxable_sales_vat,
       c.tax_paisa / 100.0 AS vat, c.tax_exempted_sales_paisa / 100.0 AS tax_exempted_sales,
       p.status AS cbms_status, p.last_code AS cbms_last_code
FROM sales_creditnote c
JOIN sales_sale s ON s.id = c.original_sale_id
JOIN tenancy_branch b ON b.id = c.branch_id
JOIN tenancy_organization o ON o.id = b.org_id
LEFT JOIN compliance_cbmspost p ON p.key = 'billreturn:cn:' || c.id
"""


def backfill_bs_dates(apps):
    """Invoices issued before BS dates were stored get them now (before the
    append-only triggers exist)."""
    import zoneinfo

    import nepali_datetime as nd

    Sale = apps.get_model("sales", "Sale")
    CreditNote = apps.get_model("sales", "CreditNote")

    def bs(dt, tz):
        if dt is None:
            return ""
        b = nd.date.from_datetime_date(dt.astimezone(tz).date())
        return f"{b.year:04d}.{b.month:02d}.{b.day:02d}"

    for s in Sale.objects.exclude(receipt_number="").filter(issued_bs="").select_related("branch"):
        tz = zoneinfo.ZoneInfo(s.branch.timezone)
        at = s.issued_at or s.paid_at or s.opened_at
        Sale.objects.filter(pk=s.pk).update(issued_at=at, issued_bs=bs(at, tz),
                                            transaction_at=s.transaction_at or at,
                                            transaction_bs=bs(s.transaction_at or at, tz))
    for c in CreditNote.objects.filter(issued_bs="").select_related("branch"):
        CreditNote.objects.filter(pk=c.pk).update(issued_bs=bs(c.at, zoneinfo.ZoneInfo(c.branch.timezone)))


def forwards(apps, schema_editor):
    backfill_bs_dates(apps)
    conn = schema_editor.connection
    with conn.cursor() as cur:
        if conn.vendor == "postgresql":
            cur.execute("""
                CREATE OR REPLACE FUNCTION ims_block_delete() RETURNS trigger AS $$
                BEGIN RAISE EXCEPTION 'IMS: % rows are append-only and cannot be deleted', TG_TABLE_NAME; END;
                $$ LANGUAGE plpgsql;
                CREATE OR REPLACE FUNCTION ims_block_update() RETURNS trigger AS $$
                BEGIN RAISE EXCEPTION 'IMS: % rows are append-only and cannot be changed', TG_TABLE_NAME; END;
                $$ LANGUAGE plpgsql;
            """)
            mutable = ", ".join(f"'{c}'" for c in SALE_MUTABLE)
            cur.execute(f"""
                CREATE OR REPLACE FUNCTION ims_guard_issued_sale() RETURNS trigger AS $$
                BEGIN
                  IF OLD.receipt_number <> '' AND
                     (to_jsonb(NEW) - ARRAY[{mutable}]) IS DISTINCT FROM (to_jsonb(OLD) - ARRAY[{mutable}]) THEN
                    RAISE EXCEPTION 'IMS: issued invoice % is append-only', OLD.receipt_number;
                  END IF;
                  IF OLD.receipt_number <> '' AND OLD.status = 'voided' AND NEW.status <> 'voided' THEN
                    RAISE EXCEPTION 'IMS: a cancelled invoice cannot be reinstated';
                  END IF;
                  RETURN NEW;
                END; $$ LANGUAGE plpgsql;
                CREATE OR REPLACE FUNCTION ims_guard_issued_child() RETURNS trigger AS $$
                BEGIN
                  IF EXISTS (SELECT 1 FROM sales_sale WHERE id = OLD.sale_id AND receipt_number <> '') THEN
                    RAISE EXCEPTION 'IMS: % belongs to an issued invoice and cannot be changed', TG_TABLE_NAME;
                  END IF;
                  RETURN NEW;
                END; $$ LANGUAGE plpgsql;
                CREATE OR REPLACE FUNCTION ims_guard_receipt() RETURNS trigger AS $$
                BEGIN
                  IF (to_jsonb(NEW) - ARRAY['print_count','printed_at'])
                     IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['print_count','printed_at']) THEN
                    RAISE EXCEPTION 'IMS: only print bookkeeping may change on a receipt';
                  END IF;
                  IF NEW.print_count < OLD.print_count THEN
                    RAISE EXCEPTION 'IMS: print count cannot go down';
                  END IF;
                  RETURN NEW;
                END; $$ LANGUAGE plpgsql;
            """)
            for t in DELETE_PROTECTED:
                cur.execute(f"CREATE TRIGGER {t}_no_delete BEFORE DELETE ON {t} "
                            f"FOR EACH ROW EXECUTE FUNCTION ims_block_delete();")
            for t in UPDATE_PROTECTED:
                cur.execute(f"CREATE TRIGGER {t}_no_update BEFORE UPDATE ON {t} "
                            f"FOR EACH ROW EXECUTE FUNCTION ims_block_update();")
            cur.execute("CREATE TRIGGER sales_sale_issued_guard BEFORE UPDATE ON sales_sale "
                        "FOR EACH ROW EXECUTE FUNCTION ims_guard_issued_sale();")
            for t in ("sales_saleline", "sales_payment"):
                cur.execute(f"CREATE TRIGGER {t}_issued_guard BEFORE UPDATE ON {t} "
                            f"FOR EACH ROW EXECUTE FUNCTION ims_guard_issued_child();")
            cur.execute("CREATE TRIGGER sales_receipt_guard BEFORE UPDATE ON sales_receipt "
                        "FOR EACH ROW EXECUTE FUNCTION ims_guard_receipt();")
            cur.execute(f"CREATE MATERIALIZED VIEW cbms_bill_view AS {BILL_VIEW_SELECT};")
            cur.execute(f"CREATE MATERIALIZED VIEW cbms_return_view AS {RETURN_VIEW_SELECT};")
            cur.execute("CREATE UNIQUE INDEX cbms_bill_view_pk ON cbms_bill_view (sale_id);")
            cur.execute("CREATE UNIQUE INDEX cbms_return_view_pk ON cbms_return_view (credit_note_id);")
        else:
            cur.execute(f"CREATE VIEW cbms_bill_view AS {BILL_VIEW_SELECT};")
            cur.execute(f"CREATE VIEW cbms_return_view AS {RETURN_VIEW_SELECT};")


def backwards(apps, schema_editor):
    conn = schema_editor.connection
    with conn.cursor() as cur:
        if conn.vendor == "postgresql":
            cur.execute("DROP MATERIALIZED VIEW IF EXISTS cbms_bill_view; "
                        "DROP MATERIALIZED VIEW IF EXISTS cbms_return_view;")
            for t in DELETE_PROTECTED:
                cur.execute(f"DROP TRIGGER IF EXISTS {t}_no_delete ON {t};")
            for t in UPDATE_PROTECTED:
                cur.execute(f"DROP TRIGGER IF EXISTS {t}_no_update ON {t};")
            cur.execute("DROP TRIGGER IF EXISTS sales_sale_issued_guard ON sales_sale;")
            for t in ("sales_saleline", "sales_payment"):
                cur.execute(f"DROP TRIGGER IF EXISTS {t}_issued_guard ON {t};")
            cur.execute("DROP TRIGGER IF EXISTS sales_receipt_guard ON sales_receipt;")
        else:
            cur.execute("DROP VIEW IF EXISTS cbms_bill_view; ")
            cur.execute("DROP VIEW IF EXISTS cbms_return_view;")


class Migration(migrations.Migration):
    dependencies = [
        ("compliance", "0002_seed_tax_categories"),
        ("sales", "0002_debitnote_documentsequence_printlog_and_more"),
        ("purchasing", "0002_goodsreceipt_claimable_vat_paisa_and_more"),
        ("admin_extras", "0002_auditlog_at_bs_auditlog_changes"),
    ]
    operations = [migrations.RunPython(forwards, backwards)]
