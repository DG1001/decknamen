"""Personenbezogene Angaben mit dem Sprachmodell finden.

Der Text geht abschnittsweise an das Modell (kleine Abschnitte übersieht es
seltener). Jeder Fund muss wörtlich im Abschnitt stehen, sonst gilt er als
erfunden und fällt weg. Namen aus früheren Abschnitten gehen als "bekannte
Personen" mit, damit das Modell ein späteres "AM" oder "Müller" der
richtigen Person zuordnet.
"""
from __future__ import annotations

import json
import re
from typing import Callable

from .kern import Fund, PLATZHALTER, TYPEN, _TITEL

BESCHREIBUNG = {
    "PERSON": "Namen von Personen: Vor- und Nachnamen, auch einzeln, Kürzel und Initialen "
              "(z. B. „AM“, „Hr. M.“), Benutzernamen",
    "EMAIL": "E-Mail-Adressen",
    "TELEFON": "Telefon-, Mobil- und Faxnummern",
    "ADRESSE": "Postanschriften: Straße mit Hausnummer, Postleitzahl mit Ort",
    "IBAN": "Bankverbindungen (IBAN, Kontonummern)",
    "KENNUNG": "persönliche Kennungen: Personal-, Kunden-, Ausweis-, Versicherungs-, "
               "Steuer-Nummern, Kfz-Kennzeichen",
    "FIRMA": "Namen von Firmen, Kunden, Lieferanten und Organisationen",
    "ORT": "Orte, Städte, Standorte",
}

# Wörter, die vor Namen stehen, aber selbst keine sind. Die Nachkontrolle sieht
# "Frau [Person 5]" und hält sonst gern "Frau" für den Rest eines Namens.
ANREDEN = {"herr", "herrn", "frau", "hr", "fr", "dr", "prof", "dipl", "ing", "mag", "mr", "mrs",
           "ms", "kollege", "kollegin", "kollegen", "team", "chef", "chefin", "leitung"}

SYSTEM = ("Du bist ein sorgfältiger Datenschutzprüfer. Du findest personenbezogene "
          "Angaben in Texten, damit sie vor der Weitergabe pseudonymisiert werden "
          "können. Lieber eine Angabe zu viel als eine zu wenig. Du antwortest nur mit JSON.")

REGELN = """Regeln:
- "text" steht Zeichen für Zeichen so im Text: gleiche Schreibweise, gleiche Groß- und Kleinschreibung.
- Jede Schreibweise einzeln aufnehmen: voller Name, Vorname allein, Nachname allein, Kürzel.
- Anreden und Titel (Herr, Frau, Dr., Prof.) gehören nicht in "text".
- Bei PERSON steht in "person" der volle Name, wenn er aus dem Text oder den bekannten Personen hervorgeht, sonst die Schreibweise selbst.
- Rollen und Funktionen ohne Namen ("die Teamleitung", "der Kunde") sind keine Funde.
- Platzhalter in eckigen Klammern wie [Person 1] sind schon ersetzt und keine Funde.
- Anreden und Titel vor einem Platzhalter ("Frau [Person 2]", "Dr. [Person 3]") sind keine Funde.
- Gibt es nichts, lautet die Antwort {"funde": []}."""


def schema(typen: set[str]) -> dict:
    return {
        "type": "object",
        "properties": {"funde": {"type": "array", "items": {
            "type": "object",
            "properties": {"text": {"type": "string"},
                           "typ": {"type": "string", "enum": sorted(typen)},
                           "person": {"type": "string"}},
            "required": ["text", "typ"]}}},
        "required": ["funde"],
    }


def abschnitte(text: str, laenge: int = 4000) -> list[str]:
    """Teilt an Leerzeilen, zu lange Absätze an Zeilen, notfalls hart."""
    def stuecke(block: str, trenner: str) -> list[str]:
        if len(block) <= laenge:
            return [block]
        if trenner == "":
            return [block[i:i + laenge] for i in range(0, len(block), laenge)]
        tiefer = "\n" if trenner == "\n\n" else ""
        teile = block.split(trenner)
        aus, akt = [], None
        for t in teile:
            for s in stuecke(t, tiefer):
                if akt is None:
                    akt = s
                elif len(akt) + len(trenner) + len(s) <= laenge:
                    akt += trenner + s
                else:
                    aus.append(akt)
                    akt = s
        if akt is not None:
            aus.append(akt)
        return aus
    return [s for s in stuecke(text, "\n\n")] if text else []


def _json(roh: str):
    roh = re.sub(r"```(?:json)?", "", roh)
    for auf, zu in (("{", "}"), ("[", "]")):
        a, b = roh.find(auf), roh.rfind(zu)
        if a != -1 and b > a:
            try:
                return json.loads(roh[a:b + 1])
            except json.JSONDecodeError:
                continue
    return None


def lies_antwort(roh: str, abschnitt: str, typen: set[str], quelle: str = "llm") -> list[Fund]:
    d = _json(roh)
    eintraege = d.get("funde", []) if isinstance(d, dict) else d if isinstance(d, list) else []
    funde = []
    for e in eintraege:
        if not isinstance(e, dict):
            continue
        text = str(e.get("text") or "").strip().strip("\"'„“")
        typ = str(e.get("typ") or "").strip().upper()
        person = str(e.get("person") or "").strip()
        if typ not in typen or typ not in TYPEN or len(text) < 2:
            continue
        if text not in abschnitt:
            ohne = _TITEL.sub("", text).strip()
            if ohne and ohne in abschnitt:
                text = ohne
            else:
                continue                       # erfunden oder verändert
        elif typ == "PERSON":
            ohne = _TITEL.sub("", text).strip()
            if ohne and ohne != text and ohne in abschnitt:
                text = ohne
        if PLATZHALTER.search(text):
            continue
        if typ == "PERSON" and text.lower().rstrip(".") in ANREDEN:
            continue                           # "Frau" vor einem Platzhalter ist kein Name
        if PLATZHALTER.search(person) or typ != "PERSON" or person == text:
            person = ""
        funde.append(Fund(text, typ, person=person, quelle=quelle))
    return funde


def _auftrag(abschnitt: str, typen: set[str], bekannte: list[str], nachkontrolle: bool) -> str:
    arten = "\n".join(f"- {t}: {BESCHREIBUNG[t]}" for t in TYPEN if t in typen)
    if nachkontrolle:
        kopf = ("Der folgende Text wurde bereits pseudonymisiert: ersetzte Angaben stehen als "
                "Platzhalter in eckigen Klammern. Prüfe, ob noch Angaben dieser Arten übrig "
                "sind, die ersetzt werden müssen:")
    else:
        kopf = "Finde im folgenden Text alle Angaben dieser Arten:"
    # Die zuletzt gefundenen zuerst; bei sehr vielen Namen nur ein Ausschnitt,
    # sonst wächst jede Anfrage mit dem Dokument mit.
    bekannte = bekannte[-60:]
    bek = ("\nBekannte Personen aus dem übrigen Dokument (für Kürzel und Nachnamen): "
           + ", ".join(bekannte) + "\n") if bekannte else ""
    return (f"{kopf}\n{arten}\n\n{REGELN}\n{bek}\n"
            'Antwortformat: {"funde": [{"text": "...", "typ": "...", "person": "..."}]}\n\n'
            f"Text:\n<<<\n{abschnitt}\n>>>")


def finde_llm(text: str, typen: set[str], frage: Callable[[str, str], str],
              laenge: int = 4000, nachkontrolle: bool = False,
              fortschritt: Callable[[int, int], None] | None = None,
              bekannte: list[str] | None = None) -> list[Fund]:
    """frage(system, nutzer) -> Antworttext des Modells."""
    typen = {t for t in typen if t in BESCHREIBUNG}
    quelle = "nachkontrolle" if nachkontrolle else "llm"
    teile = abschnitte(text, laenge)
    bekannt: list[str] = list(bekannte or [])
    alle: dict[tuple[str, str], Fund] = {}
    for i, teil in enumerate(teile):
        if fortschritt:
            fortschritt(i, len(teile))
        if not typen or not teil.strip():
            continue
        roh = frage(SYSTEM, _auftrag(teil, typen, bekannt, nachkontrolle))
        for f in lies_antwort(roh, teil, typen, quelle):
            alt = alle.get((f.text, f.typ))
            if alt is None:
                alle[(f.text, f.typ)] = f
            elif f.person and not alt.person:
                alt.person = f.person
            if f.typ == "PERSON":
                name = f.person or f.text
                if len(name.split()) >= 2 and name not in bekannt:
                    bekannt.append(name)
    if fortschritt:
        fortschritt(len(teile), len(teile))
    return list(alle.values())
