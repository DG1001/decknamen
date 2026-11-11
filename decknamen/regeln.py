"""Feste Muster: E-Mail, Telefon, IBAN und (beim Anonymisieren) Datumsangaben.

Was sich zuverlässig an der Form erkennen lässt, braucht kein Sprachmodell.
Die Regeln laufen vor dem Modell und unabhängig davon -- fällt das Modell
aus oder übersieht es etwas, sind diese Angaben trotzdem weg.
"""
from __future__ import annotations

import re

from .kern import Fund

_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}(?!\w)")

_TELEFON = re.compile(r"""
    (?<![\w+(])
    (?:
        (?:\+|00)[1-9]\d{0,2}[ ./-]?(?:\(0\)[ ]?)?\d{1,5}   # +49 711, 0049 (0)711
      | \(0\d{1,5}\)                                       # (0711)
      | 0\d{1,5}                                           # 0711
    )
    (?:[ /-]?\d{1,8}){1,5}
    (?!\w)
""", re.X)

_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,3})?\b")


def _iban_gueltig(s: str) -> bool:
    s = s.replace(" ", "")
    if not 15 <= len(s) <= 34:
        return False
    umgestellt = s[4:] + s[:4]
    zahl = "".join(str(int(c, 36)) for c in umgestellt)
    return int(zahl) % 97 == 1


def _iban(text: str):
    """(Anfang, Ende, IBAN) je gültiger IBAN."""
    for m in _IBAN.finditer(text):
        kandidat = m.group(0)
        # Hängt ein fremdes Wort am Ende ("... 00 EUR"), von hinten kürzen.
        while kandidat:
            if _iban_gueltig(kandidat):
                yield m.start(), m.start() + len(kandidat), kandidat
                break
            if " " not in kandidat:
                break
            kandidat = kandidat.rsplit(" ", 1)[0]


MONATE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
          "September", "Oktober", "November", "Dezember"]

_DATUM_WORT = re.compile(r"(?<![\w.])(\d{1,2})\.\s?(%s)(?:\s+(\d{4}))?(?!\w)" % "|".join(MONATE))
_DATUM_PUNKT = re.compile(r"(?<![\w.])(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2})?(?![\w.])")
_DATUM_ISO = re.compile(r"(?<![\w-])(\d{4})-(\d{2})-(\d{2})(?![\w-])")
_ZEILENANFANG = re.compile(r"(?:^|\n)[ \t#>*-]*$")


def _verallgemeinert(tag: int, monat: int, jahr: str | None) -> str | None:
    if not (1 <= tag <= 31 and 1 <= monat <= 12):
        return None
    drittel = "Anfang" if tag <= 10 else "Mitte" if tag <= 20 else "Ende"
    if jahr and len(jahr) == 2:
        jahr = "20" + jahr
    return f"{drittel} {MONATE[monat - 1]}" + (f" {jahr}" if jahr else "")


# "am 1.11." -> "Anfang November", nicht "am Anfang November"
_VORWORT = re.compile(r"(?<!\w)(am|vom|ab dem|seit dem)\s+$")
_VORWORT_NEU = {"am": "", "vom": "von ", "ab dem": "ab ", "seit dem": "seit "}


def _daten(text: str):
    for wortlaut, ersatz, anfang in _daten_roh(text):
        v = _VORWORT.search(text[max(0, anfang - 12):anfang])
        if v:
            yield v.group(0) + wortlaut, _VORWORT_NEU[v.group(1)] + ersatz
        else:
            yield wortlaut, ersatz


def _daten_roh(text: str):
    """(Wortlaut, Ersatz, Anfang) je Datumsangabe; Tag wird zu Anfang/Mitte/Ende des Monats."""
    for m in _DATUM_WORT.finditer(text):
        e = _verallgemeinert(int(m.group(1)), MONATE.index(m.group(2)) + 1, m.group(3))
        if e:
            yield m.group(0), e, m.start()
    for m in _DATUM_PUNKT.finditer(text):
        tag, monat, jahr = m.group(1), m.group(2), m.group(3)
        if not jahr:
            # "2.1." ist eher eine Gliederungsnummer als ein Datum, ebenso
            # alles am Zeilenanfang ("## 3.4. Personal").
            if len(tag) == 1 and len(monat) == 1:
                continue
            if _ZEILENANFANG.search(text[:m.start()]):
                continue
        e = _verallgemeinert(int(tag), int(monat), jahr)
        if e:
            yield m.group(0), e, m.start()
    for m in _DATUM_ISO.finditer(text):
        e = _verallgemeinert(int(m.group(3)), int(m.group(2)), m.group(1))
        if e:
            yield m.group(0), e, m.start()


def finde(text: str, typen: set[str]) -> list[Fund]:
    gefunden: dict[str, Fund] = {}

    def neu(t, typ):
        gefunden.setdefault(t, Fund(t, typ, quelle="regel"))

    # Ziffernfolgen in einer IBAN sind keine Telefonnummer.
    ibans = list(_iban(text))
    if "IBAN" in typen:
        for _, _, t in ibans:
            neu(t, "IBAN")
    if "EMAIL" in typen:
        for m in _EMAIL.finditer(text):
            neu(m.group(0), "EMAIL")
    if "TELEFON" in typen:
        for m in _TELEFON.finditer(text):
            if any(a < m.end() and m.start() < e for a, e, _ in ibans):
                continue
            ziffern = sum(c.isdigit() for c in m.group(0))
            if 7 <= ziffern <= 15:
                neu(m.group(0), "TELEFON")
    if "DATUM" in typen:
        for wortlaut, ersatz in _daten(text):
            gefunden.setdefault(wortlaut, Fund(wortlaut, "DATUM", quelle="regel", ersatz=ersatz))
    return list(gefunden.values())
