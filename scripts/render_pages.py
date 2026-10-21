"""Render a PDF into contact sheets (8 pages per PNG) for visual inspection.

    .venv-docs/Scripts/python scripts/render_pages.py <file.pdf> <out_dir> [dpi]
"""
import sys
from pathlib import Path

import fitz
from PIL import Image

pdf, out = Path(sys.argv[1]), Path(sys.argv[2])
dpi = int(sys.argv[3]) if len(sys.argv) > 3 else 55
out.mkdir(parents=True, exist_ok=True)
doc = fitz.open(pdf)
imgs = []
for page in doc:
    pix = page.get_pixmap(dpi=dpi)
    imgs.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
w, h = imgs[0].size
for s in range(0, len(imgs), 8):
    sheet = Image.new("RGB", (w * 4 + 50, h * 2 + 30), "#888888")
    for j, im in enumerate(imgs[s:s + 8]):
        sheet.paste(im, (10 + (j % 4) * (w + 10), 10 + (j // 4) * (h + 10)))
    sheet.save(out / f"{pdf.stem}_sheet_{s // 8 + 1}.png")
print(f"{doc.page_count} pages rendered to {out}")
