"""Ingestion worker entry point.

Batch mode: every ``*.pdf`` in ``data/documents`` is pushed through the pipeline.
The process is idempotent — chunk ids are derived from the document name, so
re-running over the same directory updates rather than duplicates.

    python -m ingestion.main                 # ingest data/documents/
    python -m ingestion.main --file x.pdf    # ingest one file
    python -m ingestion.main --dir ./docs    # ingest another directory
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent.config import get_settings
from ingestion.ocr import OcrUnavailableError, PdfExtractionError
from ingestion.pipeline import IngestionError, ingest_file


def _collect_pdfs(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.iterdir() if p.suffix.lower() == ".pdf")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ingestion.main",
        description="Ingest PDF documents into the Anchor vector store.",
    )
    parser.add_argument("--file", help="Ingest a single PDF.")
    parser.add_argument(
        "--dir",
        help="Directory of PDFs to ingest (default: data/documents).",
    )
    parser.add_argument(
        "--keep-existing",
        action="store_true",
        help="Do not delete the document's previous chunks before writing.",
    )
    args = parser.parse_args(argv)

    settings = get_settings()

    if args.file:
        targets = [Path(args.file)]
    else:
        directory = Path(args.dir) if args.dir else Path("data") / "documents"
        targets = _collect_pdfs(directory)
        if not targets:
            print(
                json.dumps(
                    {
                        "status": "no_documents",
                        "message": f"No PDFs found in '{directory}'. "
                        "Drop documents there or pass --file.",
                    },
                    indent=2,
                )
            )
            return 0

    results: list[dict[str, object]] = []
    failures = 0

    for path in targets:
        try:
            result = ingest_file(
                path, replace_existing=not args.keep_existing
            )
            results.append(result.to_dict())
        except OcrUnavailableError as exc:
            failures += 1
            results.append({"doc_name": path.name, "status": "error", "error": "ocr_unavailable",
                            "message": str(exc)})
        except PdfExtractionError as exc:
            failures += 1
            results.append({"doc_name": path.name, "status": "error", "error": "invalid_pdf",
                            "message": str(exc)})
        except IngestionError as exc:
            failures += 1
            results.append({"doc_name": path.name, "status": "error", "error": "ingestion_failed",
                            "message": str(exc)})
        except Exception as exc:  # unexpected - report, do not crash the batch
            failures += 1
            results.append({"doc_name": path.name, "status": "error", "error": type(exc).__name__,
                            "message": str(exc)})

    print(
        json.dumps(
            {
                "chroma_persist_dir": settings.CHROMA_PERSIST_DIR,
                "collection": settings.CHROMA_COLLECTION,
                "embedding_model": settings.EMBEDDING_MODEL,
                "processed": len(targets),
                "failed": failures,
                "documents": results,
            },
            indent=2,
        )
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
