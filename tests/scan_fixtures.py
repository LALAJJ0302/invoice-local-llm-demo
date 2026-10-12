"""Build scanned PDFs for the OCR tests: pages that are pictures, with no text layer.

Real scanners write several shapes of page image, and OCR has to read all of them:

- one colour or grey photo per page (most office scanners and phone apps)
- one black-and-white picture per page (fax, "B&W" scan mode)
- layered: a low-resolution colour background with the text kept as separate sharp
  black-and-white masks on top. Utility bills such as Sydney Water's arrive like this.

The text on each picture is drawn by ReportLab and rasterised by pypdfium2, both already
dependencies, so no font file or image library beyond Pillow is needed. The PDF around
the pictures is written by hand because ReportLab cannot write a 1-bit stencil mask.
"""

import io
import zlib

A4 = (595, 842)


def text_image(lines, size=A4, dpi=150, colour=None, font_size=14):
    """Lines of black text on a white (or `colour`) page, as a Pillow RGB image.

    `lines` is a list of strings, or of (x, y, text) in points from the top-left.
    """
    import pypdfium2 as pdfium
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    page = canvas.Canvas(buffer, pagesize=size)
    if colour is not None:
        page.setFillColorRGB(*colour)
        page.rect(0, 0, size[0], size[1], stroke=0, fill=1)
    page.setFillColorRGB(0, 0, 0)
    page.setFont("Helvetica", font_size)
    for index, line in enumerate(lines):
        if isinstance(line, str):
            x, y, text = 60, 80 + index * (font_size * 2), line
        else:
            x, y, text = line
        page.drawString(x, size[1] - y, text)
    page.showPage()
    page.save()

    pdf = pdfium.PdfDocument(buffer.getvalue())
    try:
        return pdf[0].render(scale=dpi / 72).to_pil().convert("RGB")
    finally:
        pdf.close()


def blank_image(size=A4, dpi=150, colour=(0.55, 0.8, 0.9)):
    """A plain coloured page with nothing written on it."""
    from PIL import Image

    width, height = round(size[0] * dpi / 72), round(size[1] * dpi / 72)
    return Image.new("RGB", (width, height), tuple(round(c * 255) for c in colour))


def _jpeg(image):
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, "JPEG", quality=85)
    width, height = image.size
    header = (
        f"/Type /XObject /Subtype /Image /Width {width} /Height {height} "
        "/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode"
    )
    return header, buffer.getvalue()


def _grey(image):
    grey = image.convert("L")
    width, height = grey.size
    header = (
        f"/Type /XObject /Subtype /Image /Width {width} /Height {height} "
        "/ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode"
    )
    return header, zlib.compress(grey.tobytes())


def _one_bit(image, stencil):
    """A 1-bit picture. As a stencil, set bits are painted in the fill colour."""
    bits = image.convert("L").point(lambda v: 255 if v > 128 else 0).convert("1")
    width, height = bits.size
    if stencil:
        # In a stencil mask 0 paints. Pillow's "1" mode stores black as 0, so black text
        # becomes the painted part and white stays transparent.
        kind = "/ImageMask true"
    else:
        kind = "/ColorSpace /DeviceGray"
    header = (
        f"/Type /XObject /Subtype /Image /Width {width} /Height {height} "
        f"{kind} /BitsPerComponent 1 /Filter /FlateDecode"
    )
    return header, zlib.compress(bits.tobytes())


def write_scan_pdf(path, pages, size=A4):
    """Write a PDF whose pages are only pictures.

    `pages` is a list of pages. A page is a dict with "layers", a list of
    (kind, image, rect), and optionally "rotate". kind is jpeg, grey, bilevel or
    stencil. rect is (x, y, width, height) in points from the bottom-left, or None
    for the whole page. Layers are painted in order, so the background comes first.
    """
    objects = []  # bytes for each object, numbered from 1

    def add(body):
        objects.append(body)
        return len(objects)

    catalog = add(b"")
    page_tree = add(b"")
    page_refs = []
    for page in pages:
        resources, drawing = [], []
        for index, (kind, image, rect) in enumerate(page["layers"]):
            if kind == "jpeg":
                header, data = _jpeg(image)
            elif kind == "grey":
                header, data = _grey(image)
            elif kind == "bilevel":
                header, data = _one_bit(image, stencil=False)
            elif kind == "stencil":
                header, data = _one_bit(image, stencil=True)
            else:
                raise ValueError(kind)
            number = add(
                f"<< {header} /Length {len(data)} >>\nstream\n".encode()
                + data
                + b"\nendstream"
            )
            name = f"Im{index}"
            resources.append(f"/{name} {number} 0 R")
            x, y, w, h = rect or (0, 0, size[0], size[1])
            fill = "0 g " if kind == "stencil" else ""
            drawing.append(f"q {fill}{w} 0 0 {h} {x} {y} cm /{name} Do Q")
        stream = "\n".join(drawing).encode()
        content = add(
            f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream"
        )
        rotate = f" /Rotate {page['rotate']}" if page.get("rotate") else ""
        page_refs.append(
            add(
                (
                    f"<< /Type /Page /Parent {page_tree} 0 R "
                    f"/MediaBox [0 0 {size[0]} {size[1]}]{rotate} "
                    f"/Resources << /XObject << {' '.join(resources)} >> >> "
                    f"/Contents {content} 0 R >>"
                ).encode()
            )
        )
    objects[catalog - 1] = f"<< /Type /Catalog /Pages {page_tree} 0 R >>".encode()
    kids = " ".join(f"{ref} 0 R" for ref in page_refs)
    objects[page_tree - 1] = (
        f"<< /Type /Pages /Kids [{kids}] /Count {len(page_refs)} >>".encode()
    )

    out = io.BytesIO()
    out.write(b"%PDF-1.5\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root {catalog} 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n".encode()
    )
    with open(path, "wb") as handle:
        handle.write(out.getvalue())
    return path


INVOICE_LINES = [
    "TAX INVOICE",
    "Harbour Utilities Pty Ltd",
    "Invoice number INV-2026-4412",
    "Date of issue 19 August 2026",
    "Water service 26.65",
    "Wastewater service 189.88",
    "Total amount due $279.40",
]


def plain_scan(path, kind="jpeg", lines=INVOICE_LINES, rotate=0, dpi=150):
    """One picture per page: a colour photo, a grey photo, or a black-and-white image."""
    image = text_image(lines, dpi=dpi, colour=(0.85, 0.93, 0.97) if kind == "jpeg" else None)
    return write_scan_pdf(path, [{"layers": [(kind, image, None)], "rotate": rotate}])


def layered_scan(path, lines=INVOICE_LINES, background_text=None):
    """A coloured background picture with the text as a sharp black-and-white mask on top.

    With `background_text`, the background carries that text and the mask is blank,
    which is how a scanner that misjudged the page would lay it out.
    """
    if background_text is None:
        background = blank_image(dpi=75)
        mask = text_image(lines, dpi=300)
    else:
        background = text_image(background_text, dpi=150, colour=(0.55, 0.8, 0.9))
        mask = text_image([], dpi=300)
    return write_scan_pdf(
        path,
        [{"layers": [("jpeg", background, None), ("stencil", mask, (20, 20, 555, 802))]}],
    )


def stamped_scan(path, lines=INVOICE_LINES):
    """A colour photo that holds the text, with a small black-and-white stamp on top.

    The stamp is 1-bit like a text mask, but it is not where the text is.
    """
    photo = text_image(lines, dpi=150, colour=(0.85, 0.93, 0.97))
    stamp = text_image([(10, 28, "PAID")], size=(120, 40), dpi=300, font_size=20)
    return write_scan_pdf(
        path,
        [{"layers": [("jpeg", photo, None), ("stencil", stamp, (440, 40, 120, 40))]}],
    )
