"""Generate a throwaway PDF containing PII, to demonstrate output redaction.

Kept out of `data/documents/` on purpose: adding a fifth document would change
the retrieval baseline the evaluation numbers were measured against. Write it
somewhere temporary, ingest it, ask a question, and watch the output guard
redact it.

    python scripts/make_pii_sample.py /tmp/pii.pdf
    curl -X POST http://localhost:8000/ingest -H "Authorization: Bearer $ADMIN" -F "file=@/tmp/pii.pdf"
    curl -X POST http://localhost:8000/query  -H "Authorization: Bearer $TOKEN" \
      -H "Content-Type: application/json" \
      -d '{"query":"What is the contact email and national insurance number?"}'
"""

from __future__ import annotations

import sys
from pathlib import Path

from fpdf import FPDF

FIELDS = [
    ("Account holder", "Dana Whitfield"),
    ("Email", "dana.whitfield@corp.example"),
    ("Direct line", "555-0142-9987"),
    ("National insurance", "123-45-6789"),
    ("Support tier", "Tier 2 - priority support"),
]


def build(path: Path) -> Path:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "Customer Support Contact Record", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    for label, value in FIELDS:
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(40, 7, label)
        pdf.set_font("Helvetica", "", 11)
        pdf.cell(0, 7, value, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(
        0,
        6,
        "Dana Whitfield is the named account holder for this support tier. The "
        "recorded contact email is dana.whitfield@corp.example and the direct "
        "line is 555-0142-9987. The national insurance number on file is "
        "123-45-6789.",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(path))
    return path


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "pii_contact_record.pdf")
    print(f"wrote {build(target)}")
