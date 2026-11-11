"""Dateien in Markdown-Text umwandeln.

Das Ergebnis ist immer reiner Text. Formatierung, eingebettete Bilder,
Kommentare und Dokumenteigenschaften (Autor, Bearbeiter) bleiben dabei
zurück -- sie würden sonst Namen am Pseudonymisieren vorbei tragen.
"""
from __future__ import annotations

import io
import pathlib

ENDUNGEN = (".md", ".markdown", ".txt", ".docx", ".pdf")


class NichtLesbar(Exception):
    pass


def lesen(name: str, daten: bytes) -> str:
    endung = pathlib.Path(name).suffix.lower()
    if endung in (".md", ".markdown", ".txt"):
        return _text(daten)
    if endung == ".docx":
        return _docx(daten)
    if endung == ".pdf":
        return _pdf(daten)
    raise NichtLesbar(f"Dateityp {endung or '(ohne Endung)'} wird nicht unterstützt "
                      f"(möglich: {', '.join(ENDUNGEN)}). Datei: {name}")


def _text(daten: bytes) -> str:
    try:
        return daten.decode("utf-8-sig")
    except UnicodeDecodeError:
        return daten.decode("cp1252", errors="replace")


# ------------------------------------------------------------------ Word

def _zelle(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def _absatz(p) -> str:
    from docx.oxml.ns import qn
    text = p.text.strip()
    if not text:
        return ""
    stil = (p.style.name if p.style is not None else "") or ""
    for vorsilbe in ("Heading ", "Überschrift "):
        if stil.startswith(vorsilbe) and stil[len(vorsilbe):].strip().isdigit():
            stufe = min(int(stil[len(vorsilbe):]), 6)
            return "#" * stufe + " " + text
    if stil == "Title":
        return "# " + text
    ppr = p._p.pPr
    if "List" in stil or "Liste" in stil or (ppr is not None and ppr.find(qn("w:numPr")) is not None):
        return "- " + text
    return text


def _tabelle(t) -> str:
    zeilen = []
    for row in t.rows:
        zellen, gesehen = [], set()
        for c in row.cells:
            if id(c._tc) in gesehen:          # verbundene Zellen kommen mehrfach
                continue
            gesehen.add(id(c._tc))
            zellen.append(_zelle(c.text))
        zeilen.append(zellen)
    if not zeilen:
        return ""
    breite = max(len(z) for z in zeilen)
    zeilen = [z + [""] * (breite - len(z)) for z in zeilen]
    aus = ["| " + " | ".join(zeilen[0]) + " |", "|" + " --- |" * breite]
    aus += ["| " + " | ".join(z) + " |" for z in zeilen[1:]]
    return "\n".join(aus)


def _docx(daten: bytes) -> str:
    import docx
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    try:
        d = docx.Document(io.BytesIO(daten))
    except Exception as e:
        raise NichtLesbar(f"Word-Datei lässt sich nicht öffnen: {e}") from e

    bloecke = []
    for el in d.element.body.iterchildren():
        if el.tag == qn("w:p"):
            bloecke.append(_absatz(Paragraph(el, d)))
        elif el.tag == qn("w:tbl"):
            bloecke.append(_tabelle(Table(el, d)))

    kopf, fuss = [], []
    for s in d.sections:
        for teil, ziel in ((s.header, kopf), (s.footer, fuss)):
            for p in teil.paragraphs:
                t = p.text.strip()
                if t and t not in ziel:
                    ziel.append(t)
    bloecke = [b for b in kopf + bloecke + fuss if b]
    aus = []
    for b in bloecke:
        # Listenpunkte hintereinander bilden eine Liste, keine Absätze.
        if aus and b.startswith("- ") and aus[-1].split("\n")[-1].startswith("- "):
            aus[-1] += "\n" + b
        else:
            aus.append(b)
    return "\n\n".join(aus) + "\n"


# ------------------------------------------------------------------- PDF

def _pdf(daten: bytes) -> str:
    from pypdf import PdfReader
    try:
        r = PdfReader(io.BytesIO(daten))
        if r.is_encrypted and not r.decrypt(""):
            raise NichtLesbar("Die PDF ist mit einem Passwort geschützt.")
        seiten = [(p.extract_text() or "").strip() for p in r.pages]
    except NichtLesbar:
        raise
    except Exception as e:
        raise NichtLesbar(f"PDF lässt sich nicht lesen: {e}") from e
    text = "\n\n".join(s for s in seiten if s)
    if len("".join(text.split())) < 20:
        raise NichtLesbar("Die PDF enthält keinen Text (eingescannt?). "
                          "Texterkennung (OCR) gehört nicht zu decknamen.")
    return text + "\n"
