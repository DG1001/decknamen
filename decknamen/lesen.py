"""Dateien in Markdown-Text umwandeln.

Das Ergebnis ist immer reiner Text. Formatierung, eingebettete Bilder,
Kommentare und Dokumenteigenschaften (Autor, Bearbeiter) bleiben dabei
zurück -- sie würden sonst Namen am Pseudonymisieren vorbei tragen.

Eingescannte PDFs und Bilder liest Tesseract, sofern installiert. Die
Seiten gehen dafür nur in ein temporäres Verzeichnis (0700), das danach
gelöscht wird.
"""
from __future__ import annotations

import io
import pathlib
import re
import shutil
import subprocess
import tempfile
from typing import Callable

BILDER = (".png", ".jpg", ".jpeg", ".tif", ".tiff")
ENDUNGEN = (".md", ".markdown", ".txt", ".docx", ".pdf") + BILDER

INSTALLATION = ("Für eingescannte PDFs und Bilder braucht decknamen Tesseract und Poppler, "
                "etwa mit „sudo apt install tesseract-ocr tesseract-ocr-deu poppler-utils“.")


class NichtLesbar(Exception):
    pass


class Texterkennung:
    """Tesseract für Bilder, pdftoppm zum Rendern von PDF-Seiten."""

    def __init__(self, sprachen: str = "deu+eng", tesseract: str = "tesseract",
                 pdftoppm: str = "pdftoppm", dpi: int = 300, zeitlimit: float = 180):
        self.tesseract, self.pdftoppm = shutil.which(tesseract), shutil.which(pdftoppm)
        self.verfuegbar = bool(self.tesseract and self.pdftoppm)
        self.dpi, self.zeitlimit = dpi, zeitlimit
        self.sprachen = sprachen
        self.version = ""
        if self.verfuegbar:
            installiert = self._sprachen()
            gewuenscht = [s for s in sprachen.split("+") if s in installiert]
            self.sprachen = "+".join(gewuenscht) or next(
                (s for s in installiert if s != "osd"), "eng")
            self.version = self._lauf([self.tesseract, "--version"]).splitlines()[0]

    def _lauf(self, befehl: list[str]) -> str:
        try:
            r = subprocess.run(befehl, capture_output=True, timeout=self.zeitlimit, check=True)
        except subprocess.TimeoutExpired as e:
            raise NichtLesbar("Die Texterkennung brauchte für eine Seite zu lange.") from e
        except (subprocess.CalledProcessError, OSError) as e:
            fehler = getattr(e, "stderr", b"") or b""
            raise NichtLesbar(f"Texterkennung fehlgeschlagen: {fehler.decode(errors='replace')[:200]}") from e
        return r.stdout.decode("utf-8", errors="replace")

    def _sprachen(self) -> list[str]:
        try:
            zeilen = self._lauf([self.tesseract, "--list-langs"]).splitlines()
        except NichtLesbar:
            return []
        return [z.strip() for z in zeilen[1:] if z.strip()]

    def bild(self, pfad: pathlib.Path) -> str:
        roh = self._lauf([self.tesseract, str(pfad), "stdout", "-l", self.sprachen, "--psm", "3"])
        return _aufraeumen(roh)

    def pdf_seite(self, pdf: pathlib.Path, seite: int, ordner: pathlib.Path) -> str:
        ziel = ordner / f"seite-{seite}"
        self._lauf([self.pdftoppm, "-r", str(self.dpi), "-gray", "-png", "-singlefile",
                    "-f", str(seite), "-l", str(seite), str(pdf), str(ziel)])
        return self.bild(ziel.with_suffix(".png"))


def _aufraeumen(text: str) -> str:
    """Trennstriche am Zeilenende zusammenziehen ("Proto-/koll"), Seitenwechsel als Leerzeile."""
    text = re.sub(r"(\w)-\n([a-zäöüß])", r"\1\2", text.replace("\f", "\n\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _leer(seite: str) -> bool:
    return len("".join(seite.split())) < 20


def braucht_ocr(name: str, daten: bytes) -> bool:
    """Ob die Datei (teilweise) nur über Texterkennung zu lesen ist."""
    endung = pathlib.Path(name).suffix.lower()
    if endung in BILDER:
        return True
    if endung != ".pdf":
        return False
    try:
        return any(_leer(s) for s in _pdf_seiten(daten))
    except NichtLesbar:
        return False


def lesen(name: str, daten: bytes, ocr: Texterkennung | None = None,
          fortschritt: Callable[[int, int], None] | None = None) -> str:
    return lesen_mit_info(name, daten, ocr, fortschritt)[0]


def lesen_mit_info(name: str, daten: bytes, ocr: Texterkennung | None = None,
                   fortschritt: Callable[[int, int], None] | None = None) -> tuple[str, list[int]]:
    """(Text, Seiten, die per Texterkennung gelesen wurden)."""
    endung = pathlib.Path(name).suffix.lower()
    if endung in (".md", ".markdown", ".txt"):
        return _text(daten), []
    if endung == ".docx":
        return _docx(daten), []
    if endung == ".pdf":
        return _pdf(daten, ocr, fortschritt)
    if endung in BILDER:
        return _bilddatei(daten, endung, ocr, fortschritt)
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

def _pdf_seiten(daten: bytes) -> list[str]:
    from pypdf import PdfReader
    try:
        r = PdfReader(io.BytesIO(daten))
        if r.is_encrypted and not r.decrypt(""):
            raise NichtLesbar("Die PDF ist mit einem Passwort geschützt.")
        return [(p.extract_text() or "").strip() for p in r.pages]
    except NichtLesbar:
        raise
    except Exception as e:
        raise NichtLesbar(f"PDF lässt sich nicht lesen: {e}") from e


def _pdf(daten: bytes, ocr: Texterkennung | None, fortschritt) -> tuple[str, list[int]]:
    seiten = _pdf_seiten(daten)
    leere = [i for i, s in enumerate(seiten) if _leer(s)]
    gelesen: list[int] = []
    if leere and ocr is not None and ocr.verfuegbar:
        with tempfile.TemporaryDirectory(prefix="decknamen-") as ordner:
            o = pathlib.Path(ordner)
            pdf = o / "eingang.pdf"
            pdf.write_bytes(daten)
            for k, i in enumerate(leere):
                if fortschritt:
                    fortschritt(k, len(leere))
                seiten[i] = ocr.pdf_seite(pdf, i + 1, o)
                gelesen.append(i + 1)
        if fortschritt:
            fortschritt(len(leere), len(leere))
    text = "\n\n".join(s for s in seiten if s)
    if _leer(text):
        if gelesen:
            raise NichtLesbar("Auch die Texterkennung hat in der PDF keinen Text gefunden.")
        raise NichtLesbar("Die PDF enthält keinen Text (eingescannt?). " + INSTALLATION)
    return text + "\n", gelesen


def _bilddatei(daten: bytes, endung: str, ocr: Texterkennung | None, fortschritt) -> tuple[str, list[int]]:
    if ocr is None or not ocr.verfuegbar:
        raise NichtLesbar("Bilder lassen sich nur per Texterkennung lesen. " + INSTALLATION)
    if fortschritt:
        fortschritt(0, 1)
    with tempfile.TemporaryDirectory(prefix="decknamen-") as ordner:
        pfad = pathlib.Path(ordner) / ("eingang" + endung)
        pfad.write_bytes(daten)
        roh = ocr._lauf([ocr.tesseract, str(pfad), "stdout", "-l", ocr.sprachen, "--psm", "3"])
    seiten = [s for s in roh.split("\f") if s.strip()] or [""]
    text = _aufraeumen(roh)
    if fortschritt:
        fortschritt(1, 1)
    if _leer(text):
        raise NichtLesbar("Die Texterkennung hat im Bild keinen Text gefunden.")
    return text + "\n", list(range(1, len(seiten) + 1))
