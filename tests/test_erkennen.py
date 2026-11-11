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


def test_besondere_angabe_bringt_ihre_neutrale_formulierung_mit():
    roh = json.dumps({"funde": [
        {"text": "krankgeschrieben", "typ": "BESONDERS", "ersatz": "abwesend"},
        {"text": "Leiterin Vertrieb Süd", "typ": "FUNKTION", "ersatz": "egal"}]})
    funde = lies_antwort(roh, "Die Leiterin Vertrieb Süd ist krankgeschrieben.", {"BESONDERS", "FUNKTION"})
    assert [(f.typ, f.text, f.ersatz) for f in funde] == [
        ("BESONDERS", "krankgeschrieben", "abwesend"), ("FUNKTION", "Leiterin Vertrieb Süd", "")]


def test_kontextarten_stehen_im_auftrag_und_im_schema():
    from decknamen.erkennen import schema
    gesehen = []
    finde_llm("Text", {"PERSON", "FUNKTION", "EINHEIT", "BESONDERS"},
              lambda s, n: gesehen.append(n) or '{"funde": []}')
    assert "FUNKTION" in gesehen[0] and "BESONDERS" in gesehen[0] and "ersatz" in gesehen[0]
    assert "ersatz" in schema({"BESONDERS"})["properties"]["funde"]["items"]["properties"]


def test_restrisiko_hinweise_mit_wortlaut_pruefung():
    from decknamen.erkennen import restrisiko
    text = "[Person 1] ist die einzige Frau im Vertrieb.\n\n[Person 2] kommt."

    def frage(system, nutzer):
        assert "Kollegin" in nutzer or "Kollege" in nutzer
        return json.dumps({"hinweise": [
            {"stelle": "die einzige Frau im Vertrieb", "grund": "eindeutig", "vorschlag": "streichen"},
            {"stelle": "erfundenes Zitat", "grund": "x", "vorschlag": "y"}]})
    h = restrisiko(text, frage)
    assert [(x["stelle"], x["gefunden"]) for x in h] == [
        ("die einzige Frau im Vertrieb", True), ("erfundenes Zitat", False)]


def test_restrisiko_zitat_gilt_trotz_zeilenumbruch_und_grossschreibung_als_gefunden():
    from decknamen.erkennen import restrisiko
    text = "Rückfragen bitte\ndirekt an [E-Mail 2], dass das Angebot offen ist."

    def frage(system, nutzer):
        return json.dumps({"hinweise": [
            {"stelle": "Rückfragen bitte direkt an [E-Mail 2]", "grund": "g", "vorschlag": "v"},
            {"stelle": "Das Angebot offen ist.", "grund": "g", "vorschlag": "v"},
            {"stelle": "Das Angebot ist offen.", "grund": "g", "vorschlag": "v"}]})
    assert [h["gefunden"] for h in restrisiko(text, frage)] == [True, True, False]


def test_restrisiko_verwirft_zitate_aus_der_eigenen_anweisung():
    from decknamen.erkennen import restrisiko

    def frage(system, nutzer):
        return json.dumps({"hinweise": [
            {"stelle": "Stell dir vor, du bist Kollegin oder Kollege in derselben Firma und kennst alle Beteiligten.",
             "grund": "g", "vorschlag": "v"},
            {"stelle": "[Person 1] kommt", "grund": "g", "vorschlag": "v"}]})
    assert [h["stelle"] for h in restrisiko("[Person 1] kommt.", frage)] == ["[Person 1] kommt"]


def test_fund_ueber_zeilenumbruch_wird_angenommen():
    roh = json.dumps({"funde": [{"text": "0731 4455-120", "typ": "TELEFON"}]})
    funde = lies_antwort(roh, "oder unter 0731\n4455-120.", {"TELEFON"})
    assert [f.text for f in funde] == ["0731 4455-120"]
