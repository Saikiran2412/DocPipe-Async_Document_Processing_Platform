import io
import os

import pypdfium2 as pdfium
import pytesseract
from PIL import Image, UnidentifiedImageError

from app.worker.errors import PermanentError

pytesseract.pytesseract.tesseract_cmd = os.getenv(
    "TESSERACT_CMD", r"C:\Program Files\Tesseract-OCR\tesseract.exe"
)
Image.MAX_IMAGE_PIXELS = 50_000_000      # guard against decompression bombs

MAX_OCR_PAGES = 10
PAGE_TIMEOUT = 60                        # seconds per page
RENDER_SCALE = 3                         # PDF points -> pixels (about 216 dpi)


def _ocr(img: Image.Image) -> str:
    try:
        return pytesseract.image_to_string(img.convert("L"), lang="eng", timeout=PAGE_TIMEOUT)
    except pytesseract.TesseractNotFoundError as exc:
        raise PermanentError("Tesseract is not installed or TESSERACT_CMD is wrong") from exc
    except RuntimeError as exc:          # pytesseract raises RuntimeError on timeout
        raise PermanentError(f"OCR failed or timed out: {exc}") from exc


def ocr_image(data: bytes) -> dict:
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as exc:
        raise PermanentError(f"could not read image: {type(exc).__name__}") from exc
    text = _ocr(img).replace("\x00", "").strip()
    return {"text": text, "stats": {"pages": 1, "ocr": True}}


def ocr_pdf(data: bytes) -> dict:
    try:
        pdf = pdfium.PdfDocument(data)
    except Exception as exc:
        raise PermanentError(f"could not open PDF for OCR: {type(exc).__name__}") from exc
    try:
        total = len(pdf)
        used = min(total, MAX_OCR_PAGES)
        parts = []
        for i in range(used):
            img = pdf[i].render(scale=RENDER_SCALE).to_pil()
            parts.append(_ocr(img))
    finally:
        pdf.close()
    text = "\n\n".join(parts).replace("\x00", "").strip()
    return {"text": text, "stats": {"pages": total, "pages_ocred": used, "ocr": True}}
