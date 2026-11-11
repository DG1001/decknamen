"""Feste Muster: E-Mail, Telefon, IBAN.

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
    return list(gefunden.values())
