import json
import os
import stat
import time

import pytest

from decknamen.auftraege import Ablage, verarbeiten, ergebnisse, zuordnung_von, \
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
