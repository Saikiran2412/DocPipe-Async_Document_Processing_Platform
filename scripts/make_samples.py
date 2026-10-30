from pathlib import Path

from pypdf import PdfWriter
from reportlab.pdfgen import canvas

out = Path("samples")
out.mkdir(exist_ok=True)

INVOICE = """Invoice No: INV-2026-001
Invoice Date: 2026-10-27
Bill to: Asha Rao <asha@example.com>

Widget x 10 ....... $1,000.00
Shipping .......... $250.00
Total due: $1,250.00 by 2026-11-30
"""

(out / "invoice.txt").write_text(INVOICE)
(out / "people.csv").write_text(
    "name,email,joined\nAsha,asha@example.com,2026-01-05\nRavi,ravi@example.com,2026-02-11\n")
(out / "empty.txt").write_text("")
(out / "note.exe").write_bytes(b"MZ not really a program")
(out / "corrupt.pdf").write_bytes(b"%PDF-1.4 this is not a real pdf")

c = canvas.Canvas(str(out / "invoice.pdf"))
y = 780
for line in INVOICE.splitlines():
    c.drawString(72, y, line)
    y -= 18
c.save()

w = PdfWriter()
w.add_blank_page(width=300, height=300)
with open(out / "blank.pdf", "wb") as f:
    w.write(f)

print("created:", sorted(p.name for p in out.iterdir()))