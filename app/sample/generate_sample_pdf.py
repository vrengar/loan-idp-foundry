"""Generate synthetic loan-application PDFs for the demo.

samples/loan_application_demo.pdf (the "missing documents" example) contains, in order:
  Pages 1-2 : Paystub (ACME Corp)
  Pages 3-4 : Bank statement (Lakeside Bank)
  Page  5   : W-2 (Wage and Tax Statement)
  Page  6   : U.S. Passport (rendered as an ID-card image)
  Page  7   : State ID / Driver License (rendered as an ID-card image)

app/examples/complete-packet.pdf holds every document the underwriting policy asks for:
two paystubs, two bank statement months, a W-2 and photo IDs.

All data is fictitious.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from reportlab.lib.colors import HexColor, black, white
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas


OUT = Path(__file__).resolve().parents[2] / "samples" / "loan_application_demo.pdf"
COMPLETE_OUT = Path(__file__).resolve().parents[2] / "app" / "examples" / "complete-packet.pdf"


def _header(c: canvas.Canvas, title: str) -> None:
    c.setFont("Helvetica-Bold", 16)
    c.drawString(1 * inch, 10.3 * inch, title)
    c.setLineWidth(0.5)
    c.line(1 * inch, 10.2 * inch, 7.5 * inch, 10.2 * inch)


def _kv(c: canvas.Canvas, x: float, y: float, k: str, v: str) -> None:
    c.setFont("Helvetica-Bold", 10); c.drawString(x, y, k)
    c.setFont("Helvetica", 10); c.drawString(x + 1.8 * inch, y, v)


def page_paystub_1(c: canvas.Canvas) -> None:
    _header(c, "ACME Corporation — Earnings Statement (Pay Stub)")
    _kv(c, 1*inch, 9.7*inch, "Employee:", "Jane Q. Borrower")
    _kv(c, 1*inch, 9.45*inch, "Employee ID:", "E-44821")
    _kv(c, 1*inch, 9.20*inch, "Pay Period:", "2026-04-01 to 2026-04-15")
    _kv(c, 1*inch, 8.95*inch, "Pay Date:", "2026-04-20")
    _kv(c, 1*inch, 8.70*inch, "Gross Pay:", "$4,250.00")
    _kv(c, 1*inch, 8.45*inch, "Net Pay:", "$3,180.55")
    _kv(c, 1*inch, 8.20*inch, "YTD Gross:", "$34,000.00")
    c.setFont("Helvetica-Bold", 11); c.drawString(1*inch, 7.7*inch, "Earnings")
    c.setFont("Helvetica", 10)
    c.drawString(1*inch, 7.45*inch, "Regular         80.00 hrs   $53.13/hr   $4,250.00")
    c.drawString(1*inch, 7.25*inch, "Overtime         0.00 hrs   $79.69/hr   $0.00")
    c.setFont("Helvetica-Bold", 11); c.drawString(1*inch, 6.7*inch, "Deductions")
    c.setFont("Helvetica", 10)
    c.drawString(1*inch, 6.45*inch, "Federal Tax     $510.00")
    c.drawString(1*inch, 6.25*inch, "Social Security $263.50")
    c.drawString(1*inch, 6.05*inch, "Medicare         $61.62")
    c.drawString(1*inch, 5.85*inch, "401(k)          $234.33")
    c.showPage()


def page_paystub_2(c: canvas.Canvas) -> None:
    _header(c, "ACME Corporation — Earnings Statement (continued)")
    c.setFont("Helvetica", 10)
    c.drawString(1*inch, 9.7*inch, "Year-to-date totals as of pay date 2026-04-20:")
    c.drawString(1*inch, 9.45*inch, "YTD Regular Earnings: $34,000.00")
    c.drawString(1*inch, 9.25*inch, "YTD Federal Tax:       $4,080.00")
    c.drawString(1*inch, 9.05*inch, "YTD Social Security:   $2,108.00")
    c.drawString(1*inch, 8.85*inch, "YTD Medicare:            $493.00")
    c.drawString(1*inch, 8.65*inch, "YTD 401(k):            $1,874.64")
    c.drawString(1*inch, 8.20*inch, "Employer: ACME Corporation, 100 Industrial Way, Springfield IL 62701")
    c.showPage()


def page_bank_1(c: canvas.Canvas) -> None:
    _header(c, "Lakeside Bank — Account Statement")
    _kv(c, 1*inch, 9.7*inch, "Account Holder:", "Jane Q. Borrower")
    _kv(c, 1*inch, 9.45*inch, "Account Number:", "****-**-3271")
    _kv(c, 1*inch, 9.20*inch, "Statement Period:", "2026-03-01 to 2026-03-31")
    _kv(c, 1*inch, 8.95*inch, "Beginning Balance:", "$8,420.10")
    _kv(c, 1*inch, 8.70*inch, "Ending Balance:", "$9,612.44")
    c.setFont("Helvetica-Bold", 11); c.drawString(1*inch, 8.2*inch, "Transaction History")
    c.setFont("Helvetica", 9)
    rows = [
        ("2026-03-02", "ACH DEPOSIT  ACME PAYROLL",  "+3,180.55"),
        ("2026-03-05", "POS  GROCERY MART",          "  -148.22"),
        ("2026-03-08", "ATM WITHDRAWAL  #4421",      "  -200.00"),
        ("2026-03-12", "ELEC BILL  CITY POWER",      "  -132.40"),
        ("2026-03-15", "TRANSFER TO SAVINGS",        "  -500.00"),
        ("2026-03-16", "ACH DEPOSIT  ACME PAYROLL",  "+3,180.55"),
        ("2026-03-20", "MORTGAGE PMT  HOMEFIRST",    "-1,945.00"),
        ("2026-03-25", "POS  FUEL STATION",          "   -62.18"),
        ("2026-03-28", "POS  RESTAURANT",            "   -41.10"),
        ("2026-03-31", "INTEREST CREDIT",            "   +0.14"),
    ]
    y = 7.95 * inch
    c.drawString(1*inch, y, "DATE          DESCRIPTION                          AMOUNT")
    y -= 0.18*inch
    for d, desc, amt in rows:
        c.drawString(1*inch, y, f"{d}    {desc:<36}{amt:>12}")
        y -= 0.18 * inch
    c.showPage()


def page_bank_2(c: canvas.Canvas) -> None:
    _header(c, "Lakeside Bank — Account Statement (continued)")
    c.setFont("Helvetica", 10)
    c.drawString(1*inch, 9.7*inch, "Average Daily Balance: $9,015.27")
    c.drawString(1*inch, 9.45*inch, "Total Deposits: $6,361.24")
    c.drawString(1*inch, 9.25*inch, "Total Withdrawals: $5,168.90")
    c.drawString(1*inch, 8.9*inch,
                 "If you have questions about this statement, contact Lakeside Bank Customer Care.")
    c.showPage()


def page_w2(c: canvas.Canvas) -> None:
    _header(c, "Form W-2 — Wage and Tax Statement (Tax Year 2025)")
    _kv(c, 1*inch, 9.7*inch, "Employee SSN:", "***-**-1234")
    _kv(c, 1*inch, 9.45*inch, "Employer Identification Number:", "12-3456789")
    _kv(c, 1*inch, 9.20*inch, "Employer:", "ACME Corporation")
    _kv(c, 1*inch, 8.95*inch, "Employer Address:", "100 Industrial Way, Springfield IL 62701")
    _kv(c, 1*inch, 8.70*inch, "Employee:", "Jane Q. Borrower")
    _kv(c, 1*inch, 8.45*inch, "Employee Address:", "742 Evergreen Ter, Springfield IL 62704")
    c.setFont("Helvetica-Bold", 11); c.drawString(1*inch, 7.9*inch, "Wage and tax information")
    c.setFont("Helvetica", 10)
    rows = [
        ("Box 1  Wages, tips, other compensation", "$102,000.00"),
        ("Box 2  Federal income tax withheld",      "$12,240.00"),
        ("Box 3  Social security wages",            "$102,000.00"),
        ("Box 4  Social security tax withheld",     "$6,324.00"),
        ("Box 5  Medicare wages and tips",          "$102,000.00"),
        ("Box 6  Medicare tax withheld",            "$1,479.00"),
    ]
    y = 7.6 * inch
    for label, amt in rows:
        c.drawString(1*inch, y, f"{label:<46}{amt:>14}")
        y -= 0.22 * inch
    c.showPage()


def _id_card(c: canvas.Canvas, *, x: float, y: float, w: float, h: float, bg_hex: str) -> None:
    """Draw a credit-card-shaped rounded rectangle as the ID background."""
    c.saveState()
    c.setFillColor(HexColor(bg_hex))
    c.setStrokeColor(black)
    c.setLineWidth(1)
    c.roundRect(x, y, w, h, 14, stroke=1, fill=1)
    c.restoreState()


def _photo_box(c: canvas.Canvas, *, x: float, y: float, w: float, h: float) -> None:
    """Draw a portrait placeholder (no real face)."""
    c.saveState()
    c.setFillColor(HexColor("#e5e7eb"))
    c.setStrokeColor(black)
    c.rect(x, y, w, h, stroke=1, fill=1)
    c.setFillColor(HexColor("#9ca3af"))
    # head
    c.circle(x + w / 2, y + h - 0.55 * inch, 0.35 * inch, stroke=0, fill=1)
    # shoulders
    c.ellipse(x + 0.15 * inch, y + 0.05 * inch, x + w - 0.15 * inch, y + h - 0.85 * inch, stroke=0, fill=1)
    c.setFillColor(black)
    c.setFont("Helvetica-Oblique", 7)
    c.drawCentredString(x + w / 2, y - 10, "PHOTO")
    c.restoreState()


def page_passport(c: canvas.Canvas) -> None:
    _header(c, "Identity Document — U.S. Passport (Image)")
    # Card
    cx, cy, cw, ch = 1 * inch, 4.5 * inch, 6.5 * inch, 4.0 * inch
    _id_card(c, x=cx, y=cy, w=cw, h=ch, bg_hex="#0b3d2e")  # dark green
    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(cx + 0.3 * inch, cy + ch - 0.45 * inch, "UNITED STATES OF AMERICA")
    c.setFont("Helvetica", 10)
    c.drawString(cx + 0.3 * inch, cy + ch - 0.65 * inch, "PASSPORT")

    # Photo
    _photo_box(c, x=cx + 0.3 * inch, y=cy + 0.5 * inch, w=1.4 * inch, h=1.9 * inch)

    # Data fields (white text on dark)
    c.setFont("Helvetica", 9); c.setFillColor(white)
    fields = [
        ("Type/Type",                 "P"),
        ("Country Code/Code",         "USA"),
        ("Passport No./No.",          "X12345678"),
        ("Surname/Nom",               "BORROWER"),
        ("Given Names/Prenoms",       "JANE QUINN"),
        ("Nationality/Nationalite",   "UNITED STATES OF AMERICA"),
        ("Date of Birth/Date de nais.","15 MAR 1986"),
        ("Sex/Sexe",                  "F"),
        ("Place of Birth/Lieu de nais.","ILLINOIS, U.S.A."),
        ("Date of Issue/Date deliv.", "10 JAN 2022"),
        ("Date of Expiration/Date exp.","09 JAN 2032"),
        ("Authority/Autorite",        "United States Department of State"),
    ]
    fx = cx + 2.0 * inch
    fy = cy + ch - 0.95 * inch
    for label, value in fields:
        c.setFont("Helvetica", 7); c.drawString(fx, fy, label)
        c.setFont("Helvetica-Bold", 10); c.drawString(fx, fy - 0.14 * inch, value)
        fy -= 0.30 * inch

    # MRZ-like band
    c.setFillColor(white)
    c.rect(cx + 0.2 * inch, cy + 0.15 * inch, cw - 0.4 * inch, 0.55 * inch, stroke=0, fill=1)
    c.setFillColor(black)
    c.setFont("Courier-Bold", 11)
    c.drawString(cx + 0.3 * inch, cy + 0.50 * inch, "P<USABORROWER<<JANE<QUINN<<<<<<<<<<<<<<<<<<<<<")
    c.drawString(cx + 0.3 * inch, cy + 0.28 * inch, "X123456784USA8603155F3201095<<<<<<<<<<<<<<04")

    c.setFillColor(black)
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(1 * inch, 4.2 * inch, "Specimen — fictitious data, not a real travel document.")
    c.showPage()


def page_drivers_license(c: canvas.Canvas) -> None:
    _header(c, "Identity Document — State ID / Driver License (Image)")
    cx, cy, cw, ch = 1 * inch, 5.0 * inch, 6.5 * inch, 3.5 * inch
    _id_card(c, x=cx, y=cy, w=cw, h=ch, bg_hex="#1d4ed8")  # blue

    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(cx + 0.3 * inch, cy + ch - 0.40 * inch, "ILLINOIS")
    c.setFont("Helvetica-Bold", 12)
    c.drawString(cx + 0.3 * inch, cy + ch - 0.62 * inch, "DRIVER LICENSE")

    # Photo
    _photo_box(c, x=cx + 0.3 * inch, y=cy + 0.4 * inch, w=1.3 * inch, h=1.7 * inch)

    fields = [
        ("DL NO",     "B123-4567-8901"),
        ("CLASS",     "D"),
        ("EXP",       "2030-03-15"),
        ("LN",        "BORROWER"),
        ("FN",        "JANE QUINN"),
        ("ADDRESS",   "742 EVERGREEN TER"),
        ("",          "SPRINGFIELD IL 62704"),
        ("DOB",       "1986-03-15"),
        ("SEX",       "F"),
        ("HGT",       "5'-06\""),
        ("EYES",      "BRN"),
        ("ISS",       "2024-03-15"),
        ("END",       "NONE"),
        ("REST",      "NONE"),
    ]
    fx = cx + 1.85 * inch
    fy = cy + ch - 0.9 * inch
    c.setFillColor(white)
    for label, value in fields:
        c.setFont("Helvetica", 7); c.drawString(fx, fy, label)
        c.setFont("Helvetica-Bold", 10); c.drawString(fx + 0.7 * inch, fy, value)
        fy -= 0.18 * inch

    # Signature line
    c.setStrokeColor(white); c.setLineWidth(0.7)
    c.line(cx + 0.3 * inch, cy + 0.30 * inch, cx + 1.6 * inch, cy + 0.30 * inch)
    c.setFont("Helvetica-Oblique", 9)
    c.drawString(cx + 0.3 * inch, cy + 0.15 * inch, "Jane Q. Borrower")

    c.setFillColor(black)
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(1 * inch, 4.7 * inch, "Specimen — fictitious data, not a real ID.")
    c.showPage()


def page_paystub_prior(c: canvas.Canvas) -> None:
    _header(c, "ACME Corporation — Earnings Statement (Pay Stub)")
    _kv(c, 1*inch, 9.7*inch, "Employee:", "Jane Q. Borrower")
    _kv(c, 1*inch, 9.45*inch, "Employee ID:", "E-44821")
    _kv(c, 1*inch, 9.20*inch, "Pay Period:", "2026-03-16 to 2026-03-31")
    _kv(c, 1*inch, 8.95*inch, "Pay Date:", "2026-04-05")
    _kv(c, 1*inch, 8.70*inch, "Gross Pay:", "$4,250.00")
    _kv(c, 1*inch, 8.45*inch, "Net Pay:", "$3,180.55")
    c.setFont("Helvetica-Bold", 11); c.drawString(1*inch, 7.7*inch, "Earnings")
    c.setFont("Helvetica", 10)
    c.drawString(1*inch, 7.45*inch, "Regular         80.00 hrs   $53.13/hr   $4,250.00")
    c.setFont("Helvetica-Bold", 11); c.drawString(1*inch, 6.7*inch, "Deductions")
    c.setFont("Helvetica", 10)
    c.drawString(1*inch, 6.45*inch, "Federal Tax     $510.00")
    c.drawString(1*inch, 6.25*inch, "Social Security $263.50")
    c.drawString(1*inch, 6.05*inch, "Medicare         $61.62")
    c.drawString(1*inch, 5.85*inch, "401(k)          $234.33")
    c.showPage()


def page_bank_feb(c: canvas.Canvas) -> None:
    _header(c, "Lakeside Bank — Account Statement")
    _kv(c, 1*inch, 9.7*inch, "Account Holder:", "Jane Q. Borrower")
    _kv(c, 1*inch, 9.45*inch, "Account Number:", "****-**-3271")
    _kv(c, 1*inch, 9.20*inch, "Statement Period:", "2026-02-01 to 2026-02-28")
    _kv(c, 1*inch, 8.95*inch, "Beginning Balance:", "$5,158.23")
    _kv(c, 1*inch, 8.70*inch, "Ending Balance:", "$8,420.10")
    c.setFont("Helvetica-Bold", 11); c.drawString(1*inch, 8.2*inch, "Transaction History")
    c.setFont("Helvetica", 9)
    rows = [
        ("2026-02-02", "ACH DEPOSIT  ACME PAYROLL",  "+3,180.55"),
        ("2026-02-04", "POS  GROCERY MART",          "  -212.40"),
        ("2026-02-06", "ATM WITHDRAWAL  #3307",      "  -200.00"),
        ("2026-02-10", "MORTGAGE PMT  HOMEFIRST",    "-1,945.00"),
        ("2026-02-13", "ELEC BILL  CITY POWER",      "  -118.75"),
        ("2026-02-16", "ACH DEPOSIT  ACME PAYROLL",  "+3,180.55"),
        ("2026-02-19", "POS  FUEL STATION",          "   -58.90"),
        ("2026-02-22", "POS  RESTAURANT",            "   -64.30"),
        ("2026-02-26", "TRANSFER TO SAVINGS",        "  -500.00"),
        ("2026-02-28", "INTEREST CREDIT",            "   +0.12"),
    ]
    y = 7.95 * inch
    c.drawString(1*inch, y, "DATE          DESCRIPTION                          AMOUNT")
    y -= 0.18*inch
    for d, desc, amt in rows:
        c.drawString(1*inch, y, f"{d}    {desc:<36}{amt:>12}")
        y -= 0.18 * inch
    c.setFont("Helvetica", 10)
    c.drawString(1*inch, y - 0.2*inch, "Total Deposits: $6,361.22")
    c.drawString(1*inch, y - 0.4*inch, "Total Withdrawals: $3,099.35")
    c.showPage()


def write_complete_packet() -> None:
    """Writes app/examples/complete-packet.pdf.

    Document types alternate (paystub, bank, paystub, bank, W-2, ID, ID) so the
    heuristic splitter, which merges adjacent pages of the same type, keeps each
    paystub and bank statement as its own segment.
    """
    _write(COMPLETE_OUT, [
        page_paystub_1, page_paystub_2, page_bank_1, page_bank_2,
        page_paystub_prior, page_bank_feb, page_w2, page_passport, page_drivers_license,
    ])


def _write(out: Path, pages: Sequence[Callable[[canvas.Canvas], None]]) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(out), pagesize=LETTER)
    for page in pages:
        page(c)
    c.save()
    print(f"Wrote {out}")


def main() -> None:
    _write(OUT, [
        page_paystub_1, page_paystub_2, page_bank_1, page_bank_2,
        page_w2, page_passport, page_drivers_license,
    ])
    write_complete_packet()


if __name__ == "__main__":
    main()
