import datetime
from decimal import Decimal
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.models import Customer, Transaction

COMPANY_NAME = "Maiders Steel Company"
PAGE_SIZE = landscape(A4)
MARGIN = 15 * mm
HEADER_HEIGHT = 24 * mm
FOOTER_HEIGHT = 12 * mm


def _money(value) -> str:
    try:
        value = Decimal(value)
    except Exception:
        return "0.00"
    return f"{value:,.2f}"


class _NumberedCanvas(Canvas):
    """Buffers every page so the footer can say 'Page X of Y' once the
    total page count is known (only available after the whole doc is built)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self._draw_footer(total_pages)
            super().showPage()
        super().save()

    def _draw_footer(self, total_pages: int):
        width, _ = PAGE_SIZE
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.grey)
        self.drawString(MARGIN, 8 * mm, COMPANY_NAME)
        self.drawRightString(
            width - MARGIN, 8 * mm, f"Page {self._pageNumber}/{total_pages}"
        )


def _make_header_drawer(customer: Customer, generated_at: datetime.datetime, filters: dict):
    def _draw_header(canvas_obj: Canvas, doc):
        width, height = PAGE_SIZE
        canvas_obj.saveState()

        canvas_obj.setFont("Helvetica-Bold", 16)
        canvas_obj.setFillColor(colors.black)
        canvas_obj.drawString(MARGIN, height - MARGIN, COMPANY_NAME)

        canvas_obj.setFont("Helvetica", 11)
        canvas_obj.drawString(MARGIN, height - MARGIN - 15, "Customer Ledger")

        canvas_obj.setFont("Helvetica", 9)
        canvas_obj.setFillColor(colors.grey)
        canvas_obj.drawRightString(
            width - MARGIN,
            height - MARGIN,
            f"Generated: {generated_at.strftime('%d %b %Y, %I:%M %p')}",
        )
        canvas_obj.setFillColor(colors.black)
        canvas_obj.setFont("Helvetica-Bold", 10)
        canvas_obj.drawRightString(
            width - MARGIN, height - MARGIN - 15, f"{customer.name} ({customer.code})"
        )

        period_bits = []
        if filters.get("date_from"):
            period_bits.append(f"From {filters['date_from']}")
        if filters.get("date_to"):
            period_bits.append(f"To {filters['date_to']}")
        if filters.get("form_id"):
            period_bits.append(f"Type: {filters['form_id']}")
        if period_bits:
            canvas_obj.setFont("Helvetica-Oblique", 8)
            canvas_obj.setFillColor(colors.grey)
            canvas_obj.drawRightString(width - MARGIN, height - MARGIN - 27, " | ".join(period_bits))

        canvas_obj.setStrokeColor(colors.grey)
        canvas_obj.setLineWidth(0.75)
        canvas_obj.line(
            MARGIN, height - MARGIN - 32, width - MARGIN, height - MARGIN - 32
        )
        canvas_obj.restoreState()

    return _draw_header


def build_customer_ledger_pdf(
    customer: Customer,
    balance: dict,
    rows: list[tuple[Transaction, Decimal]],
    filters: dict,
    generated_at: datetime.datetime,
) -> bytes:
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=PAGE_SIZE,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN + HEADER_HEIGHT,
        bottomMargin=MARGIN + FOOTER_HEIGHT - 6 * mm,
        title=f"Ledger - {customer.name}",
    )

    styles = getSampleStyleSheet()
    cell_style = ParagraphStyle(
        "cell", parent=styles["Normal"], fontSize=7.5, leading=9.5
    )

    story = []

    summary_data = [
        ["Total Debit (NGN)", "Total Credit (NGN)", "Balance (NGN)", "Transactions"],
        [
            _money(balance["total_dr"]),
            _money(balance["total_cr"]),
            _money(balance["balance"]),
            str(balance["transaction_count"]),
        ],
    ]
    summary_table = Table(summary_data, colWidths=[66 * mm] * 4)
    summary_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#343a40")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.grey),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(summary_table)
    story.append(Spacer(1, 10 * mm))

    headers = [
        "Date",
        "Details",
        "Type",
        "Ref / Invoice",
        "Payment",
        "Debit (NGN)",
        "Credit (NGN)",
        "Balance (NGN)",
    ]
    data = [headers]
    for txn, running in rows:
        data.append(
            [
                txn.date_posted.strftime("%d-%b-%Y"),
                Paragraph(escape(txn.details or "-"), cell_style),
                txn.form_id or "-",
                Paragraph(escape(txn.invoice_no or txn.ref_no or "-"), cell_style),
                txn.payment_mode or "-",
                _money(txn.amount_dr) if txn.amount_dr else "",
                _money(txn.amount_cr) if txn.amount_cr else "",
                _money(running),
            ]
        )

    if len(data) == 1:
        data.append(["-", "No transactions in this period.", "", "", "", "", "", ""])

    col_widths = [22 * mm, 68 * mm, 14 * mm, 46 * mm, 22 * mm, 28 * mm, 28 * mm, 28 * mm]
    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#212529")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 8),
                ("FONTSIZE", (0, 1), (-1, -1), 7.5),
                ("ALIGN", (0, 0), (0, -1), "CENTER"),
                ("ALIGN", (2, 0), (2, -1), "CENTER"),
                ("ALIGN", (4, 0), (4, -1), "CENTER"),
                ("ALIGN", (5, 0), (7, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#adb5bd")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f6f9")]),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(table)

    header_drawer = _make_header_drawer(customer, generated_at, filters)
    doc.build(
        story,
        onFirstPage=header_drawer,
        onLaterPages=header_drawer,
        canvasmaker=_NumberedCanvas,
    )

    return buffer.getvalue()
