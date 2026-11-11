"""Texterkennung mit dem echten Tesseract (übersprungen, wenn es fehlt)."""
import io

import pytest

from decknamen.lesen import NichtLesbar, Texterkennung, braucht_ocr, lesen, lesen_mit_info

OCR = Texterkennung()
mit_tesseract = pytest.mark.skipif(not OCR.verfuegbar, reason="tesseract oder pdftoppm fehlt")


def _bild(zeilen) -> bytes:
    """Eine Seite Text als PNG, wie von einem Scanner."""
    from PIL import Image, ImageDraw, ImageFont
    bild = Image.new("L", (1700, 900), 255)
    zeichnen = ImageDraw.Draw(bild)
    schrift = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 48)
    for i, z in enumerate(zeilen):
        zeichnen.text((100, 100 + i * 90), z, fill=0, font=schrift)
    b = io.BytesIO()
    bild.save(b, "PNG")
    return b.getvalue()


def _pdf(seiten) -> bytes:
    """Seiten sind Text (Textebene) oder bytes (eingescanntes Bild ohne Textebene)."""
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas
    b = io.BytesIO()
    c = canvas.Canvas(b, pagesize=(595, 842))
    for s in seiten:
        if isinstance(s, bytes):
            c.drawImage(ImageReader(io.BytesIO(s)), 20, 400, width=555, height=294)
        else:
            c.drawString(72, 750, s)
        c.showPage()
    c.save()
    return b.getvalue()


SCAN = ["Protokoll vom 3. Mai", "Anna Mueller leitet die Runde.", "Peter Koch schreibt mit."]


def test_was_texterkennung_braucht():
    assert braucht_ocr("scan.png", b"") and braucht_ocr("foto.JPG", b"")
    assert braucht_ocr("scan.pdf", _pdf([_bild(SCAN)]))
    assert not braucht_ocr("text.pdf", _pdf(["Anna Mueller leitet die Runde heute."]))
    assert not braucht_ocr("p.md", b"# Titel")


@mit_tesseract
def test_eingescannte_pdf_wird_gelesen():
    text, ocr_seiten = lesen_mit_info("scan.pdf", _pdf([_bild(SCAN)]), ocr=OCR)
    assert "Anna Mueller leitet die Runde" in text and "Peter Koch" in text
    assert ocr_seiten == [1]


@mit_tesseract
def test_gemischte_pdf_liest_nur_leere_seiten_per_texterkennung():
    daten = _pdf(["Seite eins hat eine Textebene mit Inhalt.", _bild(SCAN)])
    text, ocr_seiten = lesen_mit_info("gemischt.pdf", daten, ocr=OCR)
    assert text.index("Seite eins") < text.index("Anna Mueller")
    assert ocr_seiten == [2]


@mit_tesseract
def test_bilddatei_wird_gelesen():
    text, ocr_seiten = lesen_mit_info("foto.png", _bild(SCAN), ocr=OCR)
    assert "Peter Koch schreibt mit." in text and ocr_seiten == [1]


@mit_tesseract
def test_fortschritt_je_seite():
    gemeldet = []
    lesen("scan.pdf", _pdf([_bild(SCAN), _bild(SCAN)]), ocr=OCR, fortschritt=lambda i, n: gemeldet.append((i, n)))
    assert gemeldet[-1] == (2, 2) and (0, 2) in gemeldet


def test_ohne_texterkennung_kommt_ein_hinweis_zur_installation():
    with pytest.raises(NichtLesbar, match="tesseract-ocr"):
        lesen("scan.pdf", _pdf([_bild(SCAN)]))
    with pytest.raises(NichtLesbar, match="tesseract-ocr"):
        lesen("foto.png", _bild(SCAN), ocr=Texterkennung(tesseract="gibtsnicht"))


def test_sprachen_nur_soweit_installiert():
    t = Texterkennung(sprachen="deu+xyz")
    if t.verfuegbar:
        assert t.sprachen == "deu"
