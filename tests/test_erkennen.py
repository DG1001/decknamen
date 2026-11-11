import json

from decknamen.erkennen import abschnitte, lies_antwort, finde_llm


def test_antwort_mit_codezaun_und_vorrede():
    roh = 'Hier:\n```json\n{"funde": [{"text": "Anna", "typ": "person"}]}\n```'
    assert [(f.text, f.typ) for f in lies_antwort(roh, "Anna kommt.", {"PERSON"})] == [("Anna", "PERSON")]


def test_erfundenes_wird_verworfen():
    roh = json.dumps({"funde": [{"text": "Bernd", "typ": "PERSON"}, {"text": "Anna", "typ": "PERSON"}]})
    assert [f.text for f in lies_antwort(roh, "Anna kommt.", {"PERSON"})] == ["Anna"]


def test_anrede_wird_abgeschnitten_wenn_das_modell_sie_mitnimmt():
    roh = json.dumps({"funde": [{"text": "Frau Dr. Müller", "typ": "PERSON"}]})
    funde = lies_antwort(roh, "Danke an Frau Dr. Müller.", {"PERSON"})
    assert [f.text for f in funde] == ["Müller"]


def test_platzhalter_und_nicht_gewaehlte_typen_fallen_weg():
    roh = json.dumps({"funde": [{"text": "[Person 1]", "typ": "PERSON"},
                                {"text": "Stuttgart", "typ": "ORT"},
                                {"text": "Koch", "typ": "PERSON", "person": "[Person 2]"}]})
    funde = lies_antwort(roh, "[Person 1] fährt mit Koch nach Stuttgart.", {"PERSON"})
    assert [(f.text, f.person) for f in funde] == [("Koch", "")]


def test_kaputte_antwort_ergibt_nichts():
    assert lies_antwort("Ich kann das nicht.", "Anna", {"PERSON"}) == []


def test_abschnitte_halten_die_laenge_und_verlieren_nichts():
    absaetze = [f"Absatz {i} " + "x" * 50 for i in range(40)]
    text = "\n\n".join(absaetze)
    teile = abschnitte(text, laenge=500)
    assert all(len(t) <= 500 for t in teile)
    assert "\n\n".join(teile) == text


def test_ueberlanger_absatz_wird_an_zeilen_geteilt():
    text = "\n".join("Zeile %d mit Inhalt" % i for i in range(100))
    teile = abschnitte(text, laenge=300)
    assert all(len(t) <= 300 for t in teile) and len(teile) > 1
    assert "".join(t.replace("\n", "") for t in teile) == text.replace("\n", "")


def test_bekannte_personen_gehen_in_spaetere_abschnitte():
    gesehen = []

    def frage(system, nutzer):
        gesehen.append(nutzer)
        if "Anna Müller" in nutzer.split("<<<")[-1]:
            return json.dumps({"funde": [{"text": "Anna Müller", "typ": "PERSON"}]})
        return json.dumps({"funde": [{"text": "AM", "typ": "PERSON", "person": "Anna Müller"}]})

    text = "Teilnehmer: Anna Müller\n\n" + "y" * 600 + "\n\nAufgabe: AM"
    funde = finde_llm(text, {"PERSON"}, frage, laenge=300)
    assert [(f.text, f.person) for f in funde] == [("Anna Müller", ""), ("AM", "Anna Müller")]
    assert "Anna Müller" in gesehen[-1].split("<<<")[0]


def test_doppelte_funde_werden_zusammengefasst():
    def frage(system, nutzer):
        return json.dumps({"funde": [{"text": "Anna", "typ": "PERSON"}]})
    funde = finde_llm("Anna\n\n" + "z" * 400 + "\n\nAnna", {"PERSON"}, frage, laenge=200)
    assert [f.text for f in funde] == ["Anna"]


def test_nachkontrolle_sagt_dem_modell_dass_platzhalter_schon_ersetzt_sind():
    gesehen = []

    def frage(system, nutzer):
        gesehen.append(nutzer)
        return '{"funde": []}'
    finde_llm("[Person 1] kommt.", {"PERSON"}, frage, nachkontrolle=True)
    assert "bereits pseudonymisiert" in gesehen[0]
    assert all(f.quelle == "nachkontrolle" for f in
               finde_llm("Anna", {"PERSON"}, lambda s, n: '{"funde":[{"text":"Anna","typ":"PERSON"}]}',
                         nachkontrolle=True))


def test_anreden_und_titel_allein_sind_keine_personen():
    roh = json.dumps({"funde": [{"text": t, "typ": "PERSON"} for t in
                                ("Frau", "Herrn", "Dr.", "Prof", "Kollegin", "Hartmann")]})
    text = "Frau Dr. Hartmann, Herrn Prof Weber und die Kollegin"
    assert [f.text for f in lies_antwort(roh, text, {"PERSON"})] == ["Hartmann"]


def test_liste_bekannter_personen_bleibt_kurz():
    gesehen = []

    def frage(system, nutzer):
        gesehen.append(nutzer)
        return '{"funde": []}'
    viele = [f"Vorname{i} Nachname{i}" for i in range(200)]
    finde_llm("Text", {"PERSON"}, frage, bekannte=viele)
    import re
    assert len(re.findall(r"Nachname\d+", gesehen[0])) == 60
