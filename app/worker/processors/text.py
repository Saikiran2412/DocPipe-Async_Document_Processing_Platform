import csv
import io

from pypdf import PdfReader

from app.worker.errors import PermanentError
from app.worker.processors.ocr import ocr_image, ocr_pdf

MAX_BYTES = 20 * 1024 * 1024
MAX_TEXT_CHARS = 200_000
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp")


def detect_kind(filename: str, data: bytes) -> str:
    name = filename.lower()
    if data[:5] == b"%PDF-":
        return "pdf"
    if name.endswith(".pdf"):
        raise PermanentError("file has a .pdf name but is not a PDF")
    if name.endswith(IMAGE_EXTENSIONS):
        return "image"
    if name.endswith(".csv"):
        return "csv"
    if name.endswith((".txt", ".md", ".log")):
        return "text"
    raise PermanentError(f"unsupported file type: {filename}")


def _clean(text: str) -> str:
    return text.replace("\x00", "")           # JSONB cannot store NUL characters


def _decode(data: bytes) -> str:
    return data.decode("utf-8-sig", errors="replace")


def _parse_text(data: bytes) -> dict:
    text = _clean(_decode(data))
    return {"text": text[:MAX_TEXT_CHARS],
            "stats": {"chars": len(text), "lines": text.count("\n") + 1}}


def _parse_csv(data: bytes) -> dict:
    text = _clean(_decode(data))
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        raise PermanentError("CSV has no rows")
    return {"text": text[:MAX_TEXT_CHARS],
            "stats": {"rows": len(rows) - 1, "columns": rows[0][:50]}}


def _parse_image(data: bytes) -> dict:
    result = ocr_image(data)
    text = _clean(result["text"]).strip()
    if not text:
        raise PermanentError("no text found in image")
    result["stats"]["chars"] = len(text)
    return {"text": text[:MAX_TEXT_CHARS], "stats": result["stats"]}


def _parse_pdf(data: bytes) -> dict:
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        raise PermanentError("encrypted PDF")
    pages = [(page.extract_text() or "") for page in reader.pages]
    text = _clean("\n\n".join(pages)).strip()
    if text:                                   # the cheap route: the PDF has real text
        return {"text": text[:MAX_TEXT_CHARS],
                "stats": {"pages": len(pages), "chars": len(text), "ocr": False}}
    result = ocr_pdf(data)                     # no text layer: it is a scan, so OCR it
    ocr_text = _clean(result["text"]).strip()
    if not ocr_text:
        raise PermanentError("no text found, even with OCR")
    result["stats"]["chars"] = len(ocr_text)
    return {"text": ocr_text[:MAX_TEXT_CHARS], "stats": result["stats"]}


PARSERS = {"text": _parse_text, "csv": _parse_csv, "image": _parse_image, "pdf": _parse_pdf}


def extract_text(kind: str, data: bytes) -> dict:
    try:
        return PARSERS[kind](data)
    except PermanentError:
        raise
    except Exception as exc:                 # a parser crashing means a bad file
        raise PermanentError(f"could not read {kind}: {type(exc).__name__}: {exc}") from exc
