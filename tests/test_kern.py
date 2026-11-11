from decknamen.kern import Fund, zuordnen, ersetzen, zaehlen, zurueck


def _z(funde, *texte):
    return zuordnen(funde, list(texte))


def test_voller_name_und_nachname_bekommen_denselben_platzhalter():
    text = "Anna Müller eröffnet. Müller berichtet."
    z = _z([Fund("Anna Müller", "PERSON", person="Anna Müller"),
            Fund("Müller", "PERSON", person="Anna Müller")], text)
    assert ersetzen(text, z) == "[Person 1] eröffnet. [Person 1] berichtet."


def test_nachname_ohne_vollen_namen_wird_dem_passenden_vollen_namen_zugeschlagen():
    text = "Anna Müller eröffnet. Müller berichtet."
    z = _z([Fund("Anna Müller", "PERSON"), Fund("Müller", "PERSON")], text)
    assert ersetzen(text, z) == "[Person 1] eröffnet. [Person 1] berichtet."


def test_nummern_folgen_dem_ersten_auftreten():
    text = "Peter Koch und Anna Müller."
    z = _z([Fund("Anna Müller", "PERSON"), Fund("Peter Koch", "PERSON")], text)
    assert ersetzen(text, z) == "[Person 1] und [Person 2]."


def test_nur_ganze_woerter_werden_ersetzt():
    text = "Ben fragt nach Benzin."
    z = _z([Fund("Ben", "PERSON")], text)
    assert ersetzen(text, z) == "[Person 1] fragt nach Benzin."


def test_genitiv_s_bleibt_hinter_dem_platzhalter_stehen():
    text = "Müllers Vorschlag wird angenommen."
    z = _z([Fund("Müller", "PERSON")], text)
    assert ersetzen(text, z) == "[Person 1]s Vorschlag wird angenommen."


def test_kuerzel_wird_gross_klein_genau_ersetzt():
    text = "Verantwortlich: AM, am Montag."
    z = _z([Fund("AM", "PERSON", person="Anna Müller"),
            Fund("Anna Müller", "PERSON")], text + " Anna Müller")
    assert ersetzen(text, z) == "Verantwortlich: [Person 1], am Montag."


def test_namen_in_grossbuchstaben_werden_mit_ersetzt():
    text = "Gez. MÜLLER"
    z = _z([Fund("Müller", "PERSON")], "Müller " + text)
    assert ersetzen(text, z) == "Gez. [Person 1]"


def test_laengere_form_geht_vor():
    text = "Anna Müller-Lüdenscheidt und Müller"
    z = _z([Fund("Müller", "PERSON", person="Hans Müller"),
            Fund("Anna Müller-Lüdenscheidt", "PERSON")], text)
    assert ersetzen(text, z) == "[Person 1] und [Person 2]"


def test_email_bekommt_eigenen_typ():
    text = "Mail an anna.mueller@firma.de und Anna."
    z = _z([Fund("anna.mueller@firma.de", "EMAIL"), Fund("Anna", "PERSON")], text)
    assert ersetzen(text, z) == "Mail an [E-Mail 1] und [Person 1]."


def test_platzhalter_gelten_ueber_mehrere_dateien():
    a, b = "Anna Müller lädt ein.", "Peter Koch und Anna Müller."
    z = _z([Fund("Anna Müller", "PERSON"), Fund("Peter Koch", "PERSON")], a, b)
    assert ersetzen(a, z) == "[Person 1] lädt ein."
    assert ersetzen(b, z) == "[Person 2] und [Person 1]."


def test_inaktive_funde_bleiben_stehen():
    text = "Anna und Mark."
    z = _z([Fund("Anna", "PERSON"), Fund("Mark", "PERSON", aktiv=False)], text)
    assert ersetzen(text, z) == "[Person 1] und Mark."


def test_zaehlen_nennt_vorkommen_je_schreibweise():
    text = "Müller, Müllers, Anna Müller"
    z = _z([Fund("Müller", "PERSON", person="Anna Müller"), Fund("Anna Müller", "PERSON")], text)
    assert zaehlen([text], z) == {"Müller": 2, "Anna Müller": 1}


def test_zurueck_setzt_den_vollen_namen_ein():
    text = "Müller eröffnet. Anna Müller schließt."
    z = _z([Fund("Müller", "PERSON", person="Anna Müller"), Fund("Anna Müller", "PERSON")], text)
    assert zurueck("Lob an [Person 1] und [Person 1]s Team.", z) == \
        "Lob an Anna Müller und Anna Müllers Team."


def test_fund_der_im_text_nicht_vorkommt_bekommt_keine_nummer():
    text = "Anna kommt."
    z = _z([Fund("Bernd", "PERSON"), Fund("Anna", "PERSON")], text)
    assert ersetzen(text, z) == "[Person 1] kommt."


def test_genitivform_vom_modell_behaelt_ihr_s():
    text = "Sabine Hartmann eröffnet. Hartmanns Vorschlag gilt."
    z = _z([Fund("Sabine Hartmann", "PERSON"), Fund("Hartmann", "PERSON", person="Sabine Hartmann"),
            Fund("Hartmanns", "PERSON", person="Sabine Hartmann")], text)
    assert ersetzen(text, z) == "[Person 1] eröffnet. [Person 1]s Vorschlag gilt."


def test_kuerzel_ohne_zuordnung_geht_an_den_einzigen_passenden_namen():
    text = "Thomas Wagner und Tina Weber. Aufgabe: TW, Rückfrage: TWe"
    z = _z([Fund("Thomas Wagner", "PERSON"), Fund("Tina Weber", "PERSON"), Fund("TW", "PERSON")], text)
    assert ersetzen(text, z).endswith("Aufgabe: [Person 3], Rückfrage: TWe")
    text = "Thomas Wagner und Anna Roth. Aufgabe: TW"
    z = _z([Fund("Thomas Wagner", "PERSON"), Fund("Anna Roth", "PERSON"), Fund("TW", "PERSON")], text)
    assert ersetzen(text, z) == "[Person 1] und [Person 2]. Aufgabe: [Person 1]"


def test_ersatz_statt_nummer_fuer_datum_und_besondere_angaben():
    text = "Anna ist bis 31.10. krankgeschrieben, Paul seit 1.11. schwanger."
    z = _z([Fund("Anna", "PERSON"), Fund("Paul", "PERSON"),
            Fund("31.10.", "DATUM", ersatz="Ende Oktober"), Fund("1.11.", "DATUM", ersatz="Anfang November"),
            Fund("krankgeschrieben", "BESONDERS", ersatz="abwesend"), Fund("schwanger", "BESONDERS")], text)
    assert ersetzen(text, z) == ("[Person 1] ist bis Ende Oktober abwesend, "
                                 "[Person 2] seit Anfang November [besondere Angabe].")


def test_ersatzformulierungen_werden_nicht_zurueckuebersetzt():
    text = "Anna ist krankgeschrieben."
    z = _z([Fund("Anna", "PERSON"), Fund("krankgeschrieben", "BESONDERS", ersatz="abwesend")], text)
    assert zurueck("[Person 1] ist abwesend.", z) == "Anna ist abwesend."


def test_anrede_und_titel_fallen_beim_anonymisieren_weg():
    text = "Frau Dr. Yıldız rechnet, Herrn Kochs Team hilft, Prof. Weber schaut zu, Frau Koch auch."
    funde = [Fund("Yıldız", "PERSON"), Fund("Koch", "PERSON"), Fund("Weber", "PERSON")]
    z = zuordnen(funde, [text], anrede_weg=True)
    assert ersetzen(text, z) == ("[Person 1] rechnet, [Person 2]s Team hilft, "
                                 "[Person 3] schaut zu, [Person 2] auch.")
    assert zaehlen([text], z) == {"Yıldız": 1, "Koch": 2, "Weber": 1}
    assert ersetzen(text, zuordnen(funde, [text])).startswith("Frau Dr. [Person 1]")
