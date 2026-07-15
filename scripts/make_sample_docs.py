"""Generate the sample knowledge base in ``data/documents``.

The evaluation suite in ``eval/`` is built around facts stated in these
documents, so they are generated from source rather than committed as opaque
binaries: the text below is the single source of truth for what Anchor is
expected to know.

    python scripts/make_sample_docs.py [--out data/documents]

Produces four PDFs:
  it_support_policy.pdf          text layer
  hr_leave_policy.pdf            text layer
  expense_reimbursement.pdf      text layer
  scanned_facilities_procedure.pdf   image only - exercises the OCR path
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fpdf import FPDF

# --------------------------------------------------------------------------
# Source content
# --------------------------------------------------------------------------

IT_SUPPORT_POLICY = {
    "title": "IT Support Policy",
    "version": "Version 3.2 - Effective 1 January 2026",
    "sections": [
        (
            "1. Password and Account Management",
            [
                "Self-service password reset is available at "
                "https://sso.internal.example.com/reset using a registered "
                "mobile number or authenticator app.",
                "Accounts lock automatically after 5 consecutive failed sign-in "
                "attempts. A locked account unlocks by itself after 15 minutes, "
                "or an administrator can unlock it immediately from the admin console.",
                "Passwords must be at least 14 characters and are rotated every "
                "180 days. Reusing a password from the previous 5 rotations is "
                "prohibited.",
            ],
        ),
        (
            "2. Multi-Factor Authentication",
            [
                "Multi-factor authentication is mandatory for every employee and "
                "contractor with access to company systems.",
                "The approved authenticator applications are Okta Verify, Google "
                "Authenticator and Microsoft Authenticator. Hardware security keys "
                "are supported for teams that require phishing-resistant authentication.",
                "A lost or replaced phone invalidates the old enrolment. Register "
                "the replacement device before revoking the old one to avoid "
                "losing access.",
            ],
        ),
        (
            "3. VPN Access",
            [
                "The Anchor VPN client is available for Windows, macOS, iOS and "
                "Android. The supported Windows client is version 4.2 or later.",
                "VPN sessions time out after 12 hours of inactivity. Reconnect "
                "manually rather than leaving an idle session open.",
                "Split tunnelling is enabled by default. Only the internal "
                "192.168.0.0/16 range is routed through the tunnel.",
            ],
        ),
        (
            "4. Incident Priorities and Response Targets",
            [
                "A P1 incident is a complete outage of a production system for "
                "more than 10 users. P1 receives a first response within 30 minutes.",
                "A P2 incident is a major degradation or an outage affecting fewer "
                "than 10 users. P2 receives a first response within 4 business hours.",
                "A P3 request is everything else, including access requests and "
                "hardware faults. P3 receives a first response within 1 business day.",
            ],
        ),
        (
            "5. Escalation",
            [
                "Escalate to the platform on-call engineer after two failed "
                "resolution attempts, or when an incident has been open for more "
                "than 4 hours.",
                "Escalate a suspected security incident to the security team "
                "immediately. Do not wait for a second confirmation, and do not "
                "attempt remediation yourself.",
                "The on-call engineer acknowledges an escalation within 15 minutes "
                "during business hours and within 60 minutes outside them.",
            ],
        ),
        (
            "6. Hardware and Equipment",
            [
                "Laptop refresh happens every 3 years. The standard configuration "
                "is a 14-inch laptop with 16 GB of RAM and a 512 GB SSD.",
                "Additional monitors, keyboards and pointing devices are available "
                "through the equipment catalogue without line manager approval.",
                "A lost or stolen device must be reported to IT within 24 hours so "
                "it can be remotely wiped.",
            ],
        ),
    ],
}

HR_LEAVE_POLICY = {
    "title": "HR Leave and Time Off Policy",
    "version": "Version 2.1 - Effective 1 March 2026",
    "sections": [
        (
            "1. Annual Leave",
            [
                "Full-time employees accrue 25 days of paid annual leave per year, "
                "accruing monthly at 2.08 days for each completed month of service.",
                "Part-time employees accrue pro rata based on contracted hours.",
                "Unused annual leave does not roll over automatically. Up to 5 "
                "unused days may be carried into the following year and must be "
                "used before 31 March or the balance is forfeited.",
            ],
        ),
        (
            "2. Sick Leave",
            [
                "Employees receive 10 days of paid sick leave per calendar year.",
                "A medical certificate is required for any absence of 3 or more "
                "consecutive working days, and must be submitted within 7 days of "
                "returning to work.",
                "Sick leave is separate from annual leave and does not reduce the "
                "annual leave balance.",
            ],
        ),
        (
            "3. Parental Leave",
            [
                "Primary caregivers receive 18 weeks of parental leave at full pay, "
                "which may be taken in up to 3 separate blocks within the first 12 "
                "months.",
                "Secondary caregivers receive 6 weeks of parental leave at full pay "
                "and must take it within 12 months of the birth or placement.",
                "Notify your line manager and People Operations at least 8 weeks "
                "before the intended start date where possible.",
            ],
        ),
        (
            "4. Requesting Leave and Notice Periods",
            [
                "Annual leave requests of 5 or more consecutive working days "
                "require 3 weeks notice.",
                "Requests of 1 to 4 consecutive working days require 1 week notice.",
                "Leave is approved by your line manager and must be entered into "
                "the HR system before the leave begins.",
                "Leave taken during a customer critical period requires 6 weeks "
                "notice and approval from the department head.",
            ],
        ),
        (
            "5. Public Holidays",
            [
                "Public holidays follow the country of employment. Employees in "
                "the United Kingdom receive 8 bank holidays per year.",
                "Where a public holiday falls on a day you would not normally work, "
                "you may substitute it for another day, subject to line manager "
                "approval.",
            ],
        ),
    ],
}

EXPENSE_POLICY = {
    "title": "Expense Reimbursement Policy",
    "version": "Version 1.8 - Effective 1 February 2026",
    "sections": [
        (
            "1. Submission Deadlines",
            [
                "Expense claims must be submitted within 30 days of the transaction date.",
                "Claims submitted between 31 and 60 days require a written explanation "
                "from the line manager and may be rejected.",
                "Claims older than 60 days are not reimbursed under any circumstances.",
            ],
        ),
        (
            "2. Receipts",
            [
                "Itemized receipts are required for any single expense over 25 USD. "
                "Claims over 25 USD without an itemized receipt are rejected.",
                "A card slip or bank statement is not an acceptable substitute for an "
                "itemized receipt.",
                "Receipts must show the supplier, the date, the individual line items "
                "and the total amount paid.",
            ],
        ),
        (
            "3. Travel Limits",
            [
                "Hotel accommodation is capped at 200 USD per night for domestic travel "
                "and 300 USD per night for international travel.",
                "Meals during business travel are reimbursed up to 75 USD per day, "
                "regardless of the number of meals taken.",
                "Standard economy class is the default for all flights. Premium economy "
                "requires director approval, and business class is not reimbursable "
                "for flights under 8 hours.",
            ],
        ),
        (
            "4. Approval and Payment",
            [
                "Claims under 500 USD are approved by your line manager.",
                "Claims of 500 USD or more require approval from your line manager and "
                "the finance team.",
                "Approved claims are paid in the payroll run on the 15th of each month. "
                "A claim approved after the 10th is paid in the following run.",
                "Reimbursement is made in the currency of the transaction, or in USD "
                "for expenses paid in another currency.",
            ],
        ),
        (
            "5. Non-Reimbursable Expenses",
            [
                "Alcohol, minibar charges, in-flight entertainment and personal "
                "entertainment are not reimbursable.",
                "Fines, penalties and traffic violations are not reimbursable.",
                "Commuting between home and the office is not reimbursable. A home "
                "office allowance of 60 USD per month is paid separately.",
            ],
        ),
    ],
}

FACILITIES_PROCEDURE = {
    "title": "Facilities Office Access Procedure",
    "version": "Revision 4 - Issued 2026",
    "sections": [
        (
            "Visitor Registration",
            [
                "All visitors must be registered at reception before 09:00 on the day "
                "of their visit.",
                "Each visitor is issued a temporary badge that expires at 18:00 the "
                "same day.",
            ],
        ),
        (
            "Badge Access",
            [
                "Badge access is required for every floor above the second floor.",
                "Lost badges must be reported to facilities within 1 hour so the "
                "badge can be deactivated.",
            ],
        ),
        (
            "After Hours Access",
            [
                "After hours access requires manager approval recorded in the badge "
                "system before the visit.",
                "The building is locked at 22:00 on weekdays and at 18:00 on weekends.",
            ],
        ),
    ],
}

# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

PAGE_W, PAGE_H = 210.0, 297.0  # A4 in millimetres
MARGIN = 20.0

FONT_CANDIDATES = [
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/Library/Fonts/Arial.ttf",
]


def render_text_pdf(doc: dict, path: Path) -> None:
    """Write a born-digital PDF with a real text layer."""
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=MARGIN)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 10, doc["title"], new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(90, 90, 90)
    pdf.cell(0, 7, doc["version"], new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)

    for heading, paragraphs in doc["sections"]:
        pdf.ln(3)
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 8, heading, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 10.5)
        for text in paragraphs:
            pdf.multi_cell(0, 6, text, new_x="LMARGIN", new_y="NEXT")
            pdf.ln(1)

    path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(path))


def _find_font(candidates: list[str]) -> str | None:
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    return None


def render_scanned_pdf(doc: dict, path: Path) -> None:
    """Write an image-only PDF that has no text layer at all.

    This is what a phone photo of a printed procedure looks like to pypdf, and it
    is the only honest way to prove the OCR fallback actually runs.
    """
    from PIL import Image, ImageDraw, ImageFont

    font_path = _find_font(FONT_CANDIDATES)
    if font_path is None:
        raise SystemExit(
            "No TrueType font found for scanned-sample generation. Install a "
            "font (for example fonts-dejavu-core) and retry."
        )
    title_font = ImageFont.truetype(font_path, 44)
    head_font = ImageFont.truetype(font_path, 28)
    body_font = ImageFont.truetype(font_path, 24)

    # 150 DPI A4 - sharp enough that Tesseract reads it reliably.
    width, height = int(PAGE_W / 25.4 * 150), int(PAGE_H / 25.4 * 150)
    pages: list[Image.Image] = []
    current = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(current)
    y = 90

    def new_page() -> None:
        nonlocal current, draw, y
        pages.append(current)
        current = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(current)
        y = 90

    draw.text((90, y), doc["title"], font=title_font, fill="black")
    y += 60
    draw.text((90, y), doc["version"], font=body_font, fill=(80, 80, 80))
    y += 50

    for heading, paragraphs in doc["sections"]:
        if y > height - 220:
            new_page()
        draw.text((90, y), heading, font=head_font, fill="black")
        y += 42
        for text in paragraphs:
            # Greedy wrap against the measured pixel width of the font.
            words, line = text.split(), ""
            for word in words:
                candidate = f"{line} {word}".strip()
                if draw.textlength(candidate, font=body_font) > width - 180:
                    draw.text((90, y), line, font=body_font, fill="black")
                    y += 34
                    line = word
                else:
                    line = candidate
            if line:
                draw.text((90, y), line, font=body_font, fill="black")
                y += 34
            y += 12
        y += 20
    pages.append(current)

    pdf = FPDF()
    for image in pages:
        pdf.add_page(format="A4")
        pdf.image(image, x=0, y=0, w=PAGE_W, h=PAGE_H)
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(path))


SAMPLE_DOCUMENTS = [
    (IT_SUPPORT_POLICY, "it_support_policy.pdf", render_text_pdf),
    (HR_LEAVE_POLICY, "hr_leave_policy.pdf", render_text_pdf),
    (EXPENSE_POLICY, "expense_reimbursement.pdf", render_text_pdf),
    (FACILITIES_PROCEDURE, "scanned_facilities_procedure.pdf", render_scanned_pdf),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="data/documents", help="Output directory.")
    args = parser.parse_args(argv)

    out = Path(args.out)
    for doc, filename, renderer in SAMPLE_DOCUMENTS:
        target = out / filename
        renderer(doc, target)
        print(f"wrote {target} ({target.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
