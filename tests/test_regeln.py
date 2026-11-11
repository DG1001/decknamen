from decknamen.regeln import finde


def _texte(text, typen=("EMAIL", "TELEFON", "IBAN")):
    return sorted((f.typ, f.text) for f in finde(text, set(typen)))


def test_email():
    assert _texte("Bitte an a.mueller@firma-x.de schicken.") == [("EMAIL", "a.mueller@firma-x.de")]


def test_telefon_international_und_national():
    t = "Tel. +49 711 123456-78, mobil 0170 1234567 oder (0711) 98 76 54."
    assert _texte(t) == [("TELEFON", "(0711) 98 76 54"), ("TELEFON", "+49 711 123456-78"),
                         ("TELEFON", "0170 1234567")]


def test_daten_jahre_und_betraege_sind_keine_telefonnummern():
    t = "Am 01.02.2026 um 10:30, Budget 120000 EUR, Raum 0815, Jahr 2026, Version 1.2.3."
    assert _texte(t) == []


def test_iban_mit_pruefsumme():
    t = "IBAN DE89 3704 0044 0532 0130 00, falsch: DE00 1234 5678 9012 3456 78"
    assert _texte(t) == [("IBAN", "DE89 3704 0044 0532 0130 00")]


def test_nur_gewaehlte_typen():
    t = "a@b.de, 0170 1234567"
    assert _texte(t, ("EMAIL",)) == [("EMAIL", "a@b.de")]


def test_quelle_ist_regel():
    assert {f.quelle for f in finde("a@b.de", {"EMAIL"})} == {"regel"}


def test_email_vor_bindestrich_kompositum():
    assert _texte("an lukas.meier@firma.de-Verteiler") == [("EMAIL", "lukas.meier@firma.de")]


def _daten(text):
    return [(f.text, f.ersatz) for f in finde(text, {"DATUM"})]


def test_datum_ausgeschrieben_wird_auf_monatsdrittel_verallgemeinert():
    assert _daten("Teamrunde, 14. Oktober 2026") == [("14. Oktober 2026", "Mitte Oktober 2026")]


def test_datum_mit_punkten_mit_und_ohne_jahr():
    assert _daten("bis 31.10., ab 1.11.2026 und am 05.03.26") == [
        ("31.10.", "Ende Oktober"), ("1.11.2026", "Anfang November 2026"), ("am 05.03.26", "Anfang März 2026")]


def test_datum_iso():
    assert _daten("Stand 2026-10-14") == [("2026-10-14", "Mitte Oktober 2026")]


def test_nummern_die_wie_daten_aussehen_bleiben():
    t = "## 2.1. Personal\nPunkt 2.1. gilt, Version 1.2.3, 1.500 Euro, 32.10., 12.13., Raum 3.14"
    assert _daten(t) == []


def test_datum_nur_wenn_gewaehlt():
    assert finde("bis 31.10.", {"EMAIL"}) == []


def test_datum_nimmt_die_praeposition_mit_damit_der_satz_stimmt():
    assert _daten("Protokoll vom 14. Oktober 2026") == [("vom 14. Oktober 2026", "von Mitte Oktober 2026")]
    assert _daten("beginnt am 1.11. und") == [("am 1.11.", "Anfang November")]
    assert _daten("ab dem 05.03.26") == [("ab dem 05.03.26", "ab Anfang März 2026")]
    assert _daten("bis 31.10.") == [("31.10.", "Ende Oktober")]
