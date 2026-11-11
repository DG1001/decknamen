"""Der Kern: Funde zu Platzhaltern zuordnen, ersetzen und zurückübersetzen.

Ein *Fund* ist eine Schreibweise, so wie sie im Text steht ("Anna Müller",
"Müller", "AM"). Mehrere Schreibweisen derselben Person bilden eine *Gruppe*
und bekommen denselben Platzhalter ("[Person 1]"). Die Nummern folgen dem
ersten Auftreten über alle Dateien eines Auftrags hinweg, damit dieselbe
Person in jeder Datei gleich heißt.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict

# Typ -> Bezeichnung im Platzhalter
TYPEN = {
    "PERSON": "Person",
    "EMAIL": "E-Mail",
    "TELEFON": "Telefon",
    "ADRESSE": "Adresse",
    "IBAN": "IBAN",
    "KENNUNG": "Kennung",
    "FIRMA": "Firma",
    "ORT": "Ort",
    "SONSTIGES": "Angabe",
}

PLATZHALTER = re.compile(r"\[(?:%s) \d+\]" % "|".join(re.escape(v) for v in TYPEN.values()))

_KUERZEL = re.compile(r"^[A-ZÄÖÜ]{2,3}$")
_TITEL = re.compile(r"^(?:(?:herr|frau|hr|fr|dr|prof|dipl|ing|mag|mr|mrs|ms)\.?\s+)+", re.I)


@dataclass
class Fund:
    text: str                 # genau so, wie es im Text steht
    typ: str                  # Schlüssel aus TYPEN
    person: str = ""          # bei PERSON: voller Name, falls bekannt
    quelle: str = "llm"       # regel | llm | nachkontrolle | manuell
    aktiv: bool = True

    def als_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def aus_dict(cls, d: dict) -> "Fund":
        return cls(**{k: d[k] for k in ("text", "typ", "person", "quelle", "aktiv") if k in d})


@dataclass
class Gruppe:
    platzhalter: str
    typ: str
    anzeige: str              # wofür der Platzhalter steht (Zurückübersetzen)
    formen: list[str] = field(default_factory=list)


@dataclass
class Zuordnung:
    gruppen: list[Gruppe]
    form_zu_platzhalter: dict[str, str]
    muster: re.Pattern | None

    def anzeige(self) -> dict[str, str]:
        return {g.platzhalter: g.anzeige for g in self.gruppen}


def _schluessel(f: Fund) -> tuple[str, str]:
    roh = (f.person or f.text) if f.typ == "PERSON" else f.text
    if f.typ == "EMAIL":
        k = roh.strip().lower()
    elif f.typ == "TELEFON":
        k = re.sub(r"\D", "", roh)
    elif f.typ == "IBAN":
        k = re.sub(r"\s", "", roh).upper()
    else:
        k = re.sub(r"\s+", " ", _TITEL.sub("", roh.strip())).lower()
    return f.typ, k


def _varianten(form: str) -> list[str]:
    v = [form]
    if len(form) >= 3 and form.upper() != form:
        v.append(form.upper())
    return v


def _muster(formen) -> re.Pattern | None:
    formen = sorted(set(formen), key=lambda s: (-len(s), s))
    if not formen:
        return None
    alt = "|".join(re.escape(f) for f in formen)
    return re.compile(r"(?<!\w)(%s)(s?)(?!\w)" % alt)


def _erstes_auftreten(texte: list[str], formen: list[str]) -> dict[str, tuple[int, int]]:
    """Position (Datei, Stelle) des ersten Treffers je Schreibweise."""
    alias = {v: f for f in formen for v in _varianten(f)}
    m = _muster(alias)
    erste: dict[str, tuple[int, int]] = {}
    if m is None:
        return erste
    for i, t in enumerate(texte):
        for treffer in m.finditer(t):
            f = alias[treffer.group(1)]
            erste.setdefault(f, (i, treffer.start()))
    return erste


def _personen_zusammenlegen(gruppen: dict[tuple[str, str], list[Fund]]) -> dict:
    """'müller' gehört zu 'anna müller', wenn es genau einen solchen Namen gibt."""
    personen = [k for k in gruppen if k[0] == "PERSON"]
    woerter = {k: set(k[1].split()) for k in personen}
    eltern = {k: k for k in personen}

    def wurzel(k):
        while eltern[k] != k:
            k = eltern[k]
        return k

    for k in sorted(personen, key=lambda k: len(woerter[k])):
        ober = [h for h in personen if woerter[k] < woerter[h]]
        maximal = {wurzel(h) for h in ober
                   if not any(woerter[h] < woerter[x] for x in ober)}
        if len(maximal) == 1:
            eltern[k] = maximal.pop()

    neu: dict[tuple[str, str], list[Fund]] = {}
    for k, fs in gruppen.items():
        ziel = wurzel(k) if k in eltern else k
        neu.setdefault(ziel, []).extend(fs)

    # Ein Kürzel ohne Zuordnung ("TW") gehört zum einzigen vollen Namen mit
    # diesen Anfangsbuchstaben -- gibt es zwei, bleibt es für sich.
    initialen = {k: "".join(w[0] for w in k[1].split()).upper()
                 for k in neu if k[0] == "PERSON" and len(k[1].split()) >= 2}
    for k in [k for k in neu if k[0] == "PERSON"]:
        fs = neu[k]
        if all(_KUERZEL.match(f.text) and not f.person for f in fs):
            passend = [h for h, ini in initialen.items() if ini == k[1].upper() and h != k]
            if len(passend) == 1:
                neu[passend[0]].extend(neu.pop(k))
    return neu


def _genitiv_abstreifen(funde: list[Fund]) -> list[Fund]:
    """'Hartmanns' -> 'Hartmann': das s hängt das Ersetzen von selbst wieder an."""
    formen = {f.text for f in funde if f.typ == "PERSON"}
    aus = []
    for f in funde:
        if f.typ == "PERSON" and f.text.endswith("s") and len(f.text) > 3:
            grund = f.text[:-1]
            if grund in formen or grund in f.person.split():
                f = Fund(grund, f.typ, f.person, f.quelle, f.aktiv)
        aus.append(f)
    return aus


def zuordnen(funde: list[Fund], texte: list[str]) -> Zuordnung:
    aktive = [f for f in funde if f.aktiv and f.text.strip() and f.typ in TYPEN
              and not PLATZHALTER.fullmatch(f.text.strip())]
    aktive = _genitiv_abstreifen(aktive)
    erste = _erstes_auftreten(texte, [f.text for f in aktive])
    aktive = [f for f in aktive if f.text in erste]

    gruppen: dict[tuple[str, str], list[Fund]] = {}
    for f in aktive:
        gruppen.setdefault(_schluessel(f), []).append(f)
    gruppen = _personen_zusammenlegen(gruppen)

    def beginn(fs):
        return min(erste[f.text] for f in fs)

    zaehler: dict[str, int] = {}
    ergebnis: list[Gruppe] = []
    form_zu: dict[str, str] = {}
    for (typ, _), fs in sorted(gruppen.items(), key=lambda kv: beginn(kv[1])):
        zaehler[typ] = zaehler.get(typ, 0) + 1
        ph = f"[{TYPEN[typ]} {zaehler[typ]}]"
        formen = sorted({f.text for f in fs}, key=lambda s: erste[s])
        kandidaten = formen + [f.person for f in fs if f.person]
        anzeige = max(kandidaten, key=lambda s: (len(s.split()), len(s)))
        ergebnis.append(Gruppe(ph, typ, anzeige, formen))
        for form in formen:
            form_zu.setdefault(form, ph)

    alias = {v: ph for form, ph in form_zu.items() for v in _varianten(form)}
    return Zuordnung(ergebnis, alias, _muster(alias))


def ersetzen(text: str, z: Zuordnung) -> str:
    if z.muster is None:
        return text
    return z.muster.sub(lambda m: z.form_zu_platzhalter[m.group(1)] + m.group(2), text)


def stellen(text: str, z: Zuordnung):
    """Treffer als (Anfang, Ende, Schreibweise, Platzhalter) -- für die Vorschau."""
    if z.muster is None:
        return
    for m in z.muster.finditer(text):
        yield m.start(1), m.end(1), m.group(1), z.form_zu_platzhalter[m.group(1)]


def zaehlen(texte: list[str], z: Zuordnung) -> dict[str, int]:
    """Vorkommen je Schreibweise (Großschreibung zählt zur Grundform)."""
    grund = {}
    for g in z.gruppen:
        for form in g.formen:
            for v in _varianten(form):
                grund.setdefault(v, form)
    zahl: dict[str, int] = {}
    for t in texte:
        for _, _, form, _ in stellen(t, z):
            f = grund.get(form, form)
            zahl[f] = zahl.get(f, 0) + 1
    return zahl


def zurueck(text: str, z: Zuordnung) -> str:
    anzeige = z.anzeige()
    if not anzeige:
        return text
    m = re.compile("|".join(re.escape(p) for p in sorted(anzeige, key=len, reverse=True)))
    return m.sub(lambda t: anzeige[t.group(0)], text)
