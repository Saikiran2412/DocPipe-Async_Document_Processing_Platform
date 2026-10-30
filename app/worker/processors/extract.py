import re

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b")
AMOUNT = re.compile(r"(?:[$\u20ac\u00a3\u20b9]|Rs\.?\s?|INR\s?|USD\s?)\d[\d,]*(?:\.\d{1,2})?")
INVOICE = re.compile(
    r"(?i)\binvoice\s*(?:no\.?|number|#)?\s*[:\-]?\s*((?=[A-Z0-9\-]*\d)[A-Z0-9][A-Z0-9\-]{2,})"
)
LIMIT = 20


def _unique(items: list[str]) -> list[str]:
    seen, out = set(), []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
        if len(out) >= LIMIT:
            break
    return out


def extract_fields(text: str) -> dict:
    return {
        "emails": _unique(EMAIL.findall(text)),
        "dates": _unique(DATE.findall(text)),
        "amounts": _unique(AMOUNT.findall(text)),
        "invoice_numbers": _unique(INVOICE.findall(text)),
    }