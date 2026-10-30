from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

out = Path("samples")
out.mkdir(exist_ok=True)

LINES = [
    "Invoice No: INV-2026-002",
    "Invoice Date: 2026-10-28",
    "Bill to: Ravi Kumar ravi@example.com",
    "",
    "Gadget x 4 ....... $900.00",
    "Shipping .......... $80.00",
    "Total due: $980.00 by 2026-12-01",
]

font = ImageFont.truetype("arial.ttf", 38)
img = Image.new("RGB", (1200, 560), "white")
draw = ImageDraw.Draw(img)
y = 40
for line in LINES:
    draw.text((50, y), line, fill="black", font=font)
    y += 65

img.save(out / "scan.png")
img.save(out / "scan.pdf", "PDF", resolution=150)     # image-only PDF: no text layer
(out / "notimage.png").write_bytes(b"this is not an image")
print("created:", sorted(p.name for p in out.iterdir() if p.name.startswith(("scan", "notimage"))))
