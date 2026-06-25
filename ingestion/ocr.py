"""PDF text extraction with an OCR fallback.

Anchor receives a mix of born-digital PDFs (exported from Confluence, Word,
Google Docs) and scanned documents (contracts, photographed procedures). A
native text layer is far cheaper and more accurate than OCR, so the default
path is pypdf; OCR only runs on pages where the native layer came back
effectively empty.

pypdfium2 is used to rasterise pages because it ships a self-contained PDFium
binary. The common alternative (pdf2image) requires the poppler shared
libraries to be installed in every runtime image, which is a poor trade for one
function call.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import pypdf
import pypdfium2 as pdfium
import pytesseract
from PIL import Image

#: Render scale applied before OCR. 2.0 ~= 144 DPI, the usual sweet spot
#: between Tesseract accuracy and latency.
OCR_RENDER_SCALE = 2.0


class PdfExtractionError(RuntimeError):
    """The file could not be parsed as a PDF at all."""


class OcrUnavailableError(RuntimeError):
    """OCR was required but the Tesseract binary is not installed."""


@dataclass(slots=True)
class Page:
    """One extracted page and how its text was obtained."""

    page_number: int
    text: str
    ocr_used: bool = False

    @property
    def is_usable(self) -> bool:
        return bool(self.text.strip())


def configure_ocr() -> None:
    """Point pytesseract at an explicitly configured binary, if one is set.

    The Windows Tesseract installer does not add itself to PATH, so a
    ``TESSERACT_CMD`` setting is the supported escape hatch.
    """
    from agent.config import get_settings

    cmd = get_settings().TESSERACT_CMD
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd


def ocr_available() -> bool:
    """True when a working Tesseract installation can be located."""
    configure_ocr()
    try:
        pytesseract.get_tesseract_version()
    except Exception:
        return False
    return True


def tesseract_install_hint() -> str:
    return (
        "Tesseract OCR is not installed. Install it and ensure it is on PATH "
        "(Debian/Ubuntu: `apt-get install tesseract-ocr tesseract-ocr-eng`; "
        "macOS: `brew install tesseract`; Windows: install UB-Mannheim "
        "Tesseract OCR and add its directory to PATH), or set "
        "OCR_ENABLED=false to accept text-layer PDFs only."
    )


def _ocr_image(image: Image.Image, language: str) -> str:
    try:
        return pytesseract.image_to_string(image, lang=language)
    except pytesseract.TesseractNotFoundError as exc:  # pragma: no cover - env specific
        raise OcrUnavailableError(tesseract_install_hint()) from exc


def _render_page(document: pdfium.PdfDocument, index: int) -> Image.Image:
    page = document[index]
    try:
        return page.render(scale=OCR_RENDER_SCALE).to_pil()
    finally:
        # pypdfium2 pages hold native memory; release it eagerly so a 300-page
        # scan does not pin a few hundred megabytes for the whole run.
        page.close()


def extract_pages(
    source: bytes | str | Path,
    *,
    ocr_enabled: bool = True,
    min_chars_per_page: int = 40,
    ocr_language: str = "eng",
) -> list[Page]:
    """Extract text page by page, falling back to OCR where necessary.

    Returns a list of :class:`Page` records in document order. Pages that yield
    no text through either path are still returned (with empty text) so page
    numbering stays aligned with the original document for citations.
    """
    configure_ocr()
    try:
        reader = pypdf.PdfReader(io.BytesIO(source) if isinstance(source, bytes) else source)
    except Exception as exc:
        raise PdfExtractionError(
            "The uploaded file could not be read as a PDF. "
            "Verify it is a valid, non-password-protected .pdf file."
        ) from exc

    if reader.is_encrypted:
        # Many "encrypted" PDFs carry only an owner password and are readable.
        try:
            reader.decrypt("")
        except Exception as exc:
            raise PdfExtractionError(
                "The PDF is password protected and cannot be read."
            ) from exc

    pages: list[Page] = []
    try:
        pdfium_doc = pdfium.PdfDocument(io.BytesIO(source) if isinstance(source, bytes) else source)
    except Exception as exc:
        raise PdfExtractionError("The PDF could not be rendered for text extraction.") from exc

    try:
        for index in range(len(reader.pages)):
            page_number = index + 1
            native_text = ""
            try:
                native_text = reader.pages[index].extract_text() or ""
            except Exception:
                # A single unreadable page must not abort a whole ingestion run.
                native_text = ""

            if native_text.strip() and len(native_text.strip()) >= min_chars_per_page:
                pages.append(Page(page_number=page_number, text=native_text.strip()))
                continue

            if not ocr_enabled:
                pages.append(
                    Page(page_number=page_number, text=native_text.strip(), ocr_used=False)
                )
                continue

            image = _render_page(pdfium_doc, index)
            ocr_text = _ocr_image(image, ocr_language).strip()
            pages.append(Page(page_number=page_number, text=ocr_text, ocr_used=True))
    finally:
        pdfium_doc.close()

    return pages
