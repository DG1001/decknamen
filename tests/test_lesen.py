import io

import docx
import pytest

from decknamen.lesen import lesen, NichtLesbar


def _docx(bauen) -> bytes:
    d = docx.Document()
    bauen(d)
    b = io.BytesIO()
    d.save(b)
    return b.getvalue()


def test_markdown_bleibt_wie_es_ist():
    assert lesen("p.md", "# Titel\n\nAnna sagt Hallo.\n".encode()) == "# Titel\n\nAnna sagt Hallo.\n"


def test_text_mit_windows_zeichensatz():
    assert lesen("p.txt", "Müller".encode("cp1252")) == "Müller"


def test_word_ueberschriften_listen_und_absaetze():
    def bauen(d):
        d.add_heading("Protokoll", level=1)
        d.add_paragraph("Teilnehmer: Anna Müller")
        d.add_paragraph("Punkt eins", style="List Bullet")
        d.add_heading("Aufgaben", level=2)
    assert lesen("p.docx", _docx(bauen)) == \
        "# Protokoll\n\nTeilnehmer: Anna Müller\n\n- Punkt eins\n\n## Aufgaben\n"


def test_word_tabelle_wird_markdown_tabelle_in_reihenfolge():
    def bauen(d):
        d.add_paragraph("Vorher")
        t = d.add_table(rows=2, cols=2)
        t.cell(0, 0).text, t.cell(0, 1).text = "Aufgabe", "Wer"
        t.cell(1, 0).text, t.cell(1, 1).text = "Bericht | Entwurf", "AM"
        d.add_paragraph("Nachher")
    assert lesen("p.docx", _docx(bauen)) == (
        "Vorher\n\n| Aufgabe | Wer |\n| --- | --- |\n| Bericht \\| Entwurf | AM |\n\nNachher\n")


def test_word_kopf_und_fusszeile_werden_mitgenommen():
    def bauen(d):
        d.sections[0].header.paragraphs[0].text = "Erstellt von Anna Müller"
        d.add_paragraph("Inhalt")
    assert "Erstellt von Anna Müller" in lesen("p.docx", _docx(bauen))


def test_pdf_text(tmp_path):
    from reportlab.pdfgen import canvas
    b = io.BytesIO()
    c = canvas.Canvas(b)
    c.drawString(72, 750, "Protokoll vom 3. Mai")
    c.drawString(72, 730, "Anna Mueller leitet.")
    c.showPage()
    c.drawString(72, 750, "Seite zwei")
    c.save()
    text = lesen("p.pdf", b.getvalue())
    assert "Anna Mueller leitet." in text and "Seite zwei" in text


def test_pdf_ohne_text_meldet_scan():
    from reportlab.pdfgen import canvas
    b = io.BytesIO()
    c = canvas.Canvas(b)
    c.rect(10, 10, 100, 100)
    c.save()
    with pytest.raises(NichtLesbar, match="keinen Text"):
        lesen("scan.pdf", b.getvalue())


def test_unbekannte_endung():
    with pytest.raises(NichtLesbar, match="xlsx"):
        lesen("tabelle.xlsx", b"x")


def test_word_aufeinanderfolgende_listenpunkte_stehen_zusammen():
    def bauen(d):
        d.add_paragraph("eins", style="List Bullet")
        d.add_paragraph("zwei", style="List Bullet")
        d.add_paragraph("Danach")
    assert lesen("p.docx", _docx(bauen)) == "- eins\n- zwei\n\nDanach\n"
