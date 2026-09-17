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

from app.models import Customer, Supplier, SupplierTransaction, Transaction

COMPANY_NAME = "Maiders Touch Steel Company Ltd"
COMPANY_WEBSITE = "maiders.com.ng"
COMPANY_EMAIL = "info@maiders.com.ng"
COMPANY_PHONES = ["08033850151", "08108125328", "08063224169"]
PAGE_SIZE = landscape(A4)
MARGIN = 15 * mm
HEADER_HEIGHT = 28 * mm
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


def _make_header_drawer(
    party: Customer | Supplier,
    generated_at: datetime.datetime,
    filters: dict,
    ledger_title: str = "Customer Ledger",
):
    def _draw_header(canvas_obj: Canvas, doc):
        width, height = PAGE_SIZE
        canvas_obj.saveState()

        canvas_obj.setFont("Helvetica-Bold", 16)
        canvas_obj.setFillColor(colors.black)
        canvas_obj.drawString(MARGIN, height - MARGIN, COMPANY_NAME)

        canvas_obj.setFont("Helvetica", 11)
        canvas_obj.drawString(MARGIN, height - MARGIN - 15, ledger_title)

        contact_line = (
            f"Website: {COMPANY_WEBSITE}  |  Email: {COMPANY_EMAIL}  |  "
            f"Tel: {', '.join(COMPANY_PHONES)}"
        )
        canvas_obj.setFont("Helvetica", 8)
        canvas_obj.setFillColor(colors.grey)
        canvas_obj.drawString(MARGIN, height - MARGIN - 27, contact_line)

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
            width - MARGIN, height - MARGIN - 15, f"{party.name} ({party.code})"
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
            MARGIN, height - MARGIN - 36, width - MARGIN, height - MARGIN - 36
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

    net_balance = Decimal(balance["balance"])
    if net_balance > 0:
        owing_text = f"The customer {customer.name} is owing us: NGN {_money(net_balance)}."
    else:
        owing_text = f"{COMPANY_NAME} is owing {customer.name}: NGN {_money(abs(net_balance))}."
    owing_style = ParagraphStyle(
        "owing", parent=styles["Normal"], fontSize=10, leading=13, fontName="Helvetica-Bold"
    )
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(escape(owing_text), owing_style))

    header_drawer = _make_header_drawer(customer, generated_at, filters, "Customer Ledger")
    doc.build(
        story,
        onFirstPage=header_drawer,
        onLaterPages=header_drawer,
        canvasmaker=_NumberedCanvas,
    )

    return buffer.getvalue()


def build_supplier_ledger_pdf(
    supplier: Supplier,
    balance: dict,
    rows: list[tuple[SupplierTransaction, Decimal]],
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
        title=f"Supplier Ledger - {supplier.name}",
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
        "Item",
        "Payment / Account",
        "Debit (NGN)",
        "Credit (NGN)",
        "Balance (NGN)",
    ]
    data = [headers]
    for txn, running in rows:
        if txn.item_name:
            qty_bits = " ".join(
                str(b) for b in [txn.quantity, txn.measures] if b not in (None, "")
            )
            item_cell = f"{txn.item_name} ({qty_bits})" if qty_bits else txn.item_name
        else:
            item_cell = txn.vehicle_no or "-"
        payment_cell = " / ".join(
            b for b in [txn.payment_mode, txn.account_used] if b
        ) or "-"
        data.append(
            [
                txn.date_posted.strftime("%d-%b-%Y"),
                Paragraph(escape(txn.details or "-"), cell_style),
                txn.form_id or "-",
                Paragraph(escape(item_cell), cell_style),
                payment_cell,
                _money(txn.amount_dr) if txn.amount_dr else "",
                _money(txn.amount_cr) if txn.amount_cr else "",
                _money(running),
            ]
        )

    if len(data) == 1:
        data.append(["-", "No transactions in this period.", "", "", "", "", "", ""])

    col_widths = [22 * mm, 62 * mm, 14 * mm, 52 * mm, 26 * mm, 26 * mm, 26 * mm, 28 * mm]
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

    net_balance = Decimal(balance["balance"])
    if net_balance < 0:
        owing_text = f"The supplier {supplier.name} is owing us: NGN {_money(abs(net_balance))}."
    else:
        owing_text = (
            f"{COMPANY_NAME} is owing the supplier {supplier.name}: NGN {_money(net_balance)}."
        )
    owing_style = ParagraphStyle(
        "owing", parent=styles["Normal"], fontSize=10, leading=13, fontName="Helvetica-Bold"
    )
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(escape(owing_text), owing_style))

    header_drawer = _make_header_drawer(supplier, generated_at, filters, "Supplier Ledger")
    doc.build(
        story,
        onFirstPage=header_drawer,
        onLaterPages=header_drawer,
        canvasmaker=_NumberedCanvas,
    )

    return buffer.getvalue()
