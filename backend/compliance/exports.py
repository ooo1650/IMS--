"""Export any report (see reports.py shape) to Excel, XML, PDF or CSV
(payment.md Step 2: "Export to Excel, XML and PDF")."""
import csv
import io
import zipfile
from xml.etree import ElementTree as ET

from django.http import HttpResponse

CONTENT_TYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xml": "application/xml",
    "pdf": "application/pdf",
    "csv": "text/csv",
}


FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _value(v, kind, for_text=False):
    if kind == "money":
        return f"{(v or 0) / 100:.2f}" if for_text else round((v or 0) / 100, 2)
    if v is None:
        return ""
    # Text typed at the till (buyer names, product names) must not become a
    # spreadsheet formula when the accountant opens the export (CSV injection).
    if isinstance(v, str) and v.startswith(FORMULA_START):
        return "'" + v
    return v


def to_xlsx(report) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = report["title"][:31].replace("/", "-")
    ws.append([report["title"]])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append([report.get("subtitle", "")])
    ws.append([])
    ws.append([label for _, label, _ in report["columns"]])
    for c in ws[4]:
        c.font = Font(bold=True)
    for row in report["rows"]:
        ws.append([_value(row.get(k), kind) for k, _, kind in report["columns"]])
    for col, (_, _, kind) in enumerate(report["columns"], start=1):
        if kind == "money":
            for cell in ws.iter_rows(min_row=5, min_col=col, max_col=col):
                cell[0].number_format = "#,##0.00"
    if report.get("totals"):
        ws.append([("Total" if i == 0 else _value(report["totals"].get(k), kind) if k in report["totals"] else "")
                   for i, (k, _, kind) in enumerate(report["columns"])])
        for c in ws[ws.max_row]:
            c.font = Font(bold=True)
    for note in report.get("notes", []):
        ws.append([note])
    for col in ws.columns:
        width = max((len(str(c.value)) for c in col if c.value is not None and c.row > 3), default=8)
        ws.column_dimensions[col[0].column_letter].width = min(max(10, width + 2), 60)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def to_xml(report) -> bytes:
    root = ET.Element("report", title=report["title"], period=report.get("subtitle", ""))
    rows_el = ET.SubElement(root, "rows")
    for row in report["rows"]:
        r = ET.SubElement(rows_el, "row")
        for k, _, kind in report["columns"]:
            ET.SubElement(r, k).text = str(_value(row.get(k), kind, for_text=True))
    if report.get("totals"):
        t = ET.SubElement(root, "totals")
        kinds = {k: kind for k, _, kind in report["columns"]}
        for k, v in report["totals"].items():
            ET.SubElement(t, k).text = str(_value(v, kinds.get(k, "money"), for_text=True))
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def to_csv(report) -> bytes:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow([label for _, label, _ in report["columns"]])
    for row in report["rows"]:
        w.writerow([_value(row.get(k), kind, for_text=True) for k, _, kind in report["columns"]])
    return out.getvalue().encode("utf-8-sig")


def to_pdf(report) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    from .documents import _register_fonts, mixed

    _register_fonts()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=10 * mm, rightMargin=10 * mm,
                            topMargin=10 * mm, bottomMargin=10 * mm, title=report["title"])
    small = ParagraphStyle("s", fontName="Helvetica", fontSize=7, leading=9)
    head = ParagraphStyle("h", fontName="Helvetica-Bold", fontSize=13, leading=16)
    data = [[Paragraph(f"<b>{label}</b>", small) for _, label, _ in report["columns"]]]
    for row in report["rows"]:
        data.append([Paragraph(mixed(_value(row.get(k), kind, for_text=True)), small)
                     for k, _, kind in report["columns"]])
    if report.get("totals"):
        data.append([Paragraph("<b>Total</b>" if i == 0 else
                               (f"<b>{_value(report['totals'][k], kind, True)}</b>" if k in report["totals"] else ""),
                               small) for i, (k, _, kind) in enumerate(report["columns"])])
    t = Table(data, repeatRows=1)
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
                           ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
                           ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story = [Paragraph(mixed(report["title"]), head), Paragraph(mixed(report.get("subtitle", "")), small),
             Spacer(1, 4 * mm), t]
    for note in report.get("notes", []):
        story += [Spacer(1, 2 * mm), Paragraph(mixed(note), small)]
    doc.build(story)
    return buf.getvalue()


RENDERERS = {"xlsx": to_xlsx, "xml": to_xml, "pdf": to_pdf, "csv": to_csv}


def response(report, fmt, filename):
    resp = HttpResponse(RENDERERS[fmt](report), content_type=CONTENT_TYPES[fmt])
    resp["Content-Disposition"] = f'attachment; filename="{filename}.{fmt}"'
    return resp


def zip_reports(reports: dict) -> bytes:
    """{filename_stem: report} -> zip of xlsx files (accountant export)."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for stem, report in reports.items():
            z.writestr(f"{stem}.xlsx", to_xlsx(report))
            z.writestr(f"{stem}.xml", to_xml(report))
    return out.getvalue()
