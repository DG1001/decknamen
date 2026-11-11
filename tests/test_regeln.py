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
