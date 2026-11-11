import json
import os
import stat
import time

import pytest

from decknamen.auftraege import Ablage, verarbeiten, ergebnisse, zuordnung_von, bericht_markdown, bericht_erstellen, \
    funde_setzen, fund_hinzufuegen, zuordnung_export, NichtGefunden


class FalschesLLM:
    """Antwortet mit festen Funden; merkt sich, was es gefragt wurde."""

    def __init__(self, personen=(), nachkontrolle=(), fehler=None):
        self.personen, self.nach, self.fehler = list(personen), list(nachkontrolle), fehler
        self.fragen = []

    def modell_name(self):
        return "falsch-7b"

    def chat(self, system, nutzer, schema=None, max_tokens=4096):
        if self.fehler:
            raise self.fehler
        self.fragen.append(nutzer)
        abschnitt = nutzer.split("<<<\n", 1)[1]
        namen = self.nach if "bereits pseudonymisiert" in nutzer else self.personen
        return json.dumps({"funde": [{"text": n, "typ": "PERSON"} for n in namen if n in abschnitt]})


@pytest.fixture
def ablage(tmp_path):
    return Ablage(tmp_path / "daten")


PROTOKOLL = ("# Protokoll\n\nTeilnehmer: Anna Müller, Peter Koch\n\n"
             "Müller berichtet. Rückfragen an anna.mueller@firma.de oder Herrn Kochs Team. "
             "Gast: Lüdenscheidt\n")


def _auftrag(ablage, dateien=None, typen=("PERSON", "EMAIL"), nachkontrolle=True):
    return ablage.neu(dateien or [("Protokoll_Anna_Müller.docx", PROTOKOLL)], list(typen), nachkontrolle)


def test_ganzer_ablauf_regeln_modell_und_nachkontrolle(ablage):
    a = _auftrag(ablage)
    llm = FalschesLLM(personen=["Anna Müller", "Peter Koch", "Müller", "Koch"],
                      nachkontrolle=["Lüdenscheidt"])
    verarbeiten(ablage, a["id"], llm)
    a = ablage.laden(a["id"])
    assert a["stand"] == "pruefen" and a["modell"] == "falsch-7b"
    quellen = {(f["text"], f["quelle"]) for f in a["funde"]}
    assert ("anna.mueller@firma.de", "regel") in quellen
    assert ("Lüdenscheidt", "nachkontrolle") in quellen
    [(name, text)] = ergebnisse(ablage, a)
    assert text == ("# Protokoll\n\nTeilnehmer: [Person 1], [Person 2]\n\n"
                    "[Person 1] berichtet. Rückfragen an [E-Mail 1] oder Herrn [Person 2]s Team. "
                    "Gast: [Person 3]\n")
    assert name == "Protokoll_[Person 1].md"


def test_dieselbe_person_heisst_in_allen_dateien_gleich(ablage):
    a = _auftrag(ablage, [("a.md", "Peter Koch lädt ein."), ("b.md", "Anna Müller und Peter Koch.")],
                 nachkontrolle=False)
    verarbeiten(ablage, a["id"], FalschesLLM(personen=["Anna Müller", "Peter Koch"]))
    texte = [t for _, t in ergebnisse(ablage, ablage.laden(a["id"]))]
    assert texte == ["[Person 1] lädt ein.", "[Person 2] und [Person 1]."]


def test_spaetere_datei_kennt_die_namen_der_frueheren(ablage):
    a = _auftrag(ablage, [("a.md", "Anna Müller lädt ein."), ("b.md", "AM schreibt.")],
                 nachkontrolle=False)
    llm = FalschesLLM(personen=["Anna Müller"])
    verarbeiten(ablage, a["id"], llm)
    assert "Anna Müller" in llm.fragen[-1].split("<<<")[0]


def test_fehler_des_modells_landet_im_auftrag(ablage):
    from decknamen.llm import LLMFehler
    a = _auftrag(ablage)
    verarbeiten(ablage, a["id"], FalschesLLM(fehler=LLMFehler("nicht erreichbar")))
    a = ablage.laden(a["id"])
    assert a["stand"] == "fehler" and "nicht erreichbar" in a["fehler"]


def test_ohne_modell_laufen_nur_die_regeln(ablage):
    a = _auftrag(ablage)
    verarbeiten(ablage, a["id"], None)
    a = ablage.laden(a["id"])
    assert a["stand"] == "pruefen"
    assert {f["quelle"] for f in a["funde"]} == {"regel"}


def test_haken_entfernen_und_begriff_ergaenzen(ablage):
    a = _auftrag(ablage, nachkontrolle=False)
    verarbeiten(ablage, a["id"], FalschesLLM(personen=["Anna Müller", "Koch"]))
    a = ablage.laden(a["id"])
    koch = next(i for i, f in enumerate(a["funde"]) if f["text"] == "Koch")
    funde_setzen(ablage, a, aktiv={i for i in range(len(a["funde"])) if i != koch})
    fund_hinzufuegen(ablage, a, "Lüdenscheidt", "PERSON", "")
    fund_hinzufuegen(ablage, a, "Müller", "PERSON", "Anna Müller")
    [(_, text)] = ergebnisse(ablage, ablage.laden(a["id"]))
    assert "Kochs Team" in text and "Gast: [Person 2]" in text
    assert "[Person 1] berichtet" in text


def test_begriff_der_nirgends_vorkommt_wird_abgelehnt(ablage):
    a = _auftrag(ablage, nachkontrolle=False)
    verarbeiten(ablage, a["id"], None)
    with pytest.raises(NichtGefunden):
        fund_hinzufuegen(ablage, ablage.laden(a["id"]), "Bernd", "PERSON", "")


def test_zuordnung_export_fuer_spaeteres_zurueckuebersetzen(ablage):
    a = _auftrag(ablage, nachkontrolle=False)
    verarbeiten(ablage, a["id"], FalschesLLM(personen=["Anna Müller", "Müller"]))
    d = zuordnung_export(ablage.laden(a["id"]), ablage)
    assert d["[Person 1]"]["steht_fuer"] == "Anna Müller"
    assert set(d["[Person 1]"]["schreibweisen"]) == {"Anna Müller", "Müller"}


def test_ablage_ist_nur_fuer_den_eigentuemer_lesbar(ablage):
    a = _auftrag(ablage)
    ordner = ablage.wurzel / a["id"]
    assert stat.S_IMODE(os.stat(ordner).st_mode) == 0o700
    for p in ordner.rglob("*"):
        if p.is_file():
            assert stat.S_IMODE(os.stat(p).st_mode) == 0o600


def test_alte_auftraege_werden_aufgeraeumt(ablage):
    alt, neu = _auftrag(ablage), _auftrag(ablage)
    d = ablage.laden(alt["id"])
    d["erstellt"] = time.time() - 8 * 86400
    ablage.speichern(d)
    assert ablage.aufraeumen(tage=7) == 1
    assert [x["id"] for x in ablage.alle()] == [neu["id"]]


def test_zweck_bei_der_belegung_verraet_keine_dateinamen(ablage):
    gemeldet = []

    class Stelle:
        def halten(self, zweck, **k):
            gemeldet.append(zweck)
            import contextlib
            return contextlib.nullcontext()

    a = _auftrag(ablage, nachkontrolle=False)
    verarbeiten(ablage, a["id"], FalschesLLM(), belegung=Stelle())
    assert gemeldet and "Müller" not in gemeldet[0] and "Protokoll" not in gemeldet[0]


# ------------------------------------------------------------ Anonymisieren

class AnonymLLM(FalschesLLM):
    """Meldet Personen, eine Funktion, eine besondere Angabe und Restrisiken."""

    def chat(self, system, nutzer, schema=None, max_tokens=4096):
        self.fragen.append(nutzer)
        abschnitt = nutzer.split("<<<\n", 1)[1]
        if "Kollegin oder Kollege" in nutzer:
            return json.dumps({"hinweise": [{"stelle": "einzige Frau im Team",
                                             "grund": "eindeutig", "vorschlag": "weglassen"}]})
        funde = [{"text": n, "typ": "PERSON"} for n in ("Anna Müller", "Müller") if n in abschnitt]
        if "Teamleiterin" in abschnitt:
            funde.append({"text": "Teamleiterin", "typ": "FUNKTION"})
        if "krankgeschrieben" in abschnitt:
            funde.append({"text": "krankgeschrieben", "typ": "BESONDERS", "ersatz": "abwesend"})
        return json.dumps({"funde": funde})


ANONYM = ("Protokoll vom 14. Oktober 2026\n\nFrau Dr. Anna Müller, unsere Teamleiterin, ist bis 31.10. "
          "krankgeschrieben. Frau Müller ist die einzige Frau im Team.\n")


def _anonym(ablage):
    a = ablage.neu([("p.md", ANONYM)], ["PERSON"], True, modus="anonym")
    verarbeiten(ablage, a["id"], AnonymLLM())
    return ablage.laden(a["id"])


def test_anonymisieren_nimmt_kontext_daten_und_anreden_mit(ablage):
    a = _anonym(ablage)
    assert a["modus"] == "anonym" and {"FUNKTION", "EINHEIT", "BESONDERS", "DATUM"} <= set(a["typen"])
    [(_, text)] = ergebnisse(ablage, a)
    assert text == ("Protokoll von Mitte Oktober 2026\n\n[Person 1], unsere [Funktion 1], ist bis "
                    "Ende Oktober abwesend. [Person 1] ist die einzige Frau im Team.\n")


def test_anonymisieren_erstellt_restrisiko_bericht(ablage):
    a = _anonym(ablage)
    b = a["bericht"]
    assert b["modell"] == "falsch-7b" and b["stand"] == a["funde_stand"]
    assert [(h["datei"], h["stelle"], h["gefunden"]) for h in b["hinweise"]] == [(0, "einzige Frau im Team", True)]
    md = bericht_markdown(ablage, a)
    assert "einzige Frau im Team" in md and "keine Bestätigung" in md
    assert "- Verallgemeinert: 2 Datumsangaben, 1 besondere Angabe\n" in md
    assert "Anna" not in md and "Müller" not in md


def test_bericht_veraltet_nach_aenderungen(ablage):
    a = _anonym(ablage)
    funde_setzen(ablage, a, aktiv=set(range(len(a["funde"]))))
    a = ablage.laden(a["id"])
    assert a["bericht"]["stand"] != a["funde_stand"]
    bericht_erstellen(ablage, a["id"], AnonymLLM())
    a = ablage.laden(a["id"])
    assert a["bericht"]["stand"] == a["funde_stand"] and a["stand"] == "pruefen"


def test_anonym_gibt_keine_zuordnung_heraus(ablage):
    a = _anonym(ablage)
    with pytest.raises(PermissionError):
        zuordnung_export(a, ablage)


def test_anonyme_auftraege_halten_hoechstens_einen_tag(ablage):
    a = _anonym(ablage)
    a["erstellt"] = time.time() - 25 * 3600
    ablage.speichern(a)
    assert ablage.aufraeumen(tage=7) == 1


def test_pseudonymisieren_bleibt_wie_es_war(ablage):
    a = ablage.neu([("p.md", ANONYM)], ["PERSON", "FUNKTION", "DATUM"], True)
    verarbeiten(ablage, a["id"], AnonymLLM())
    a = ablage.laden(a["id"])
    assert a["modus"] == "pseudonym" and a["typen"] == ["PERSON"] and a["bericht"] is None
    [(_, text)] = ergebnisse(ablage, a)
    assert text.startswith("Protokoll vom 14. Oktober 2026\n\nFrau Dr. [Person 1], unsere Teamleiterin")


# ------------------------------------------------------------ Texterkennung

from test_texterkennung import OCR, SCAN, _bild, _pdf, mit_tesseract  # noqa: E402


@mit_tesseract
def test_eingescannte_datei_wird_im_auftrag_gelesen(ablage):
    a = ablage.neu([("scan.pdf", None, _pdf([_bild(SCAN)]))], ["PERSON"], False)
    assert list((ablage.wurzel / a["id"] / "roh").iterdir())
    verarbeiten(ablage, a["id"], FalschesLLM(personen=["Anna Mueller", "Peter Koch"]), ocr=OCR)
    a = ablage.laden(a["id"])
    assert a["stand"] == "pruefen" and a["dateien"][0]["ocr_seiten"] == [1]
    assert a["dateien"][0]["zeichen"] > 30
    assert not list((ablage.wurzel / a["id"] / "roh").iterdir())     # Rohdatei ist weg
    [(_, text)] = ergebnisse(ablage, a)
    assert "[Person 1] leitet die Runde." in text and "[Person 2] schreibt mit." in text


def test_eingescannte_datei_ohne_texterkennung_bricht_mit_hinweis_ab(ablage):
    a = ablage.neu([("scan.pdf", None, _pdf([_bild(SCAN)]))], ["PERSON"], False)
    verarbeiten(ablage, a["id"], FalschesLLM(), ocr=None)
    a = ablage.laden(a["id"])
    assert a["stand"] == "fehler" and "tesseract-ocr" in a["fehler"]
    assert list((ablage.wurzel / a["id"] / "roh").iterdir())           # für einen neuen Versuch
