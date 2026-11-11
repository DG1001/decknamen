import base64
import io
import json
import re
import zipfile

import pytest

from decknamen.auftraege import Ablage, verarbeiten, bericht_erstellen
from decknamen.web import app_bauen

from test_auftraege import FalschesLLM, AnonymLLM, ANONYM


class SofortWerkstatt:
    def __init__(self, ablage, llm):
        self.ablage, self.llm = ablage, llm
        self.auftraege = []

    def einreihen(self, auftrag_id, art="erkennen"):
        self.auftraege.append(art)
        if art == "bericht":
            bericht_erstellen(self.ablage, auftrag_id, self.llm)
        else:
            verarbeiten(self.ablage, auftrag_id, self.llm)


TEXT = "# Protokoll\n\nAnna Müller eröffnet. Müller bittet Peter Koch um Rückmeldung an a.m@firma.de.\n"


@pytest.fixture
def umgebung(tmp_path):
    ablage = Ablage(tmp_path / "daten")
    llm = FalschesLLM(personen=["Anna Müller", "Müller", "Peter Koch"])
    app = app_bauen(ablage, SofortWerkstatt(ablage, llm),
                    lambda: {"basis": "http://127.0.0.1:8889/v1", "modell": "falsch-7b", "fehler": ""})
    app.testing = True
    return app.test_client(), ablage


def _hochladen(c, name="Protokoll.md", inhalt=TEXT, headers=None, **felder):
    daten = {"dateien": (io.BytesIO(inhalt.encode()), name),
             "typ": ["PERSON", "EMAIL", "TELEFON"], "nachkontrolle": "1", **felder}
    return c.post("/auftrag", data=daten, content_type="multipart/form-data", headers=headers or {})


def _auftrag_id(antwort):
    return re.search(r"/auftrag/([\w-]+)", antwort.headers["Location"]).group(1)


def test_startseite_laedt_nichts_von_aussen(umgebung):
    c, _ = umgebung
    r = c.get("/")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "falsch-7b" in html
    assert not re.search(r'(src|href)="(https?:)?//', html)
    csp = r.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp and "connect-src 'self'" in csp
    assert r.headers["Cache-Control"] == "no-store"


def test_hochladen_pruefen_herunterladen(umgebung):
    c, _ = umgebung
    r = _hochladen(c)
    assert r.status_code == 303
    aid = _auftrag_id(r)
    seite = c.get(f"/auftrag/{aid}").get_data(as_text=True)
    assert "[Person 1]" in seite and "Anna Müller" in seite and "[E-Mail 1]" in seite
    assert '<mark class="band t-person" title="Anna Müller">[Person 1]</mark>' in seite
    assert '<mark class="fund t-person" title="[Person 2]">Peter Koch</mark>' in seite
    datei = c.get(f"/auftrag/{aid}/datei/0")
    assert datei.get_data(as_text=True) == (
        "# Protokoll\n\n[Person 1] eröffnet. [Person 1] bittet [Person 2] um Rückmeldung an [E-Mail 1].\n")
    assert "attachment" in datei.headers["Content-Disposition"]
    z = zipfile.ZipFile(io.BytesIO(c.get(f"/auftrag/{aid}/alle.zip").data))
    assert z.namelist() == ["Protokoll.md"]
    zuo = json.loads(c.get(f"/auftrag/{aid}/zuordnung.json").data)
    assert zuo["[Person 2]"]["steht_fuer"] == "Peter Koch"


def test_stand_als_json(umgebung):
    c, _ = umgebung
    aid = _auftrag_id(_hochladen(c))
    d = c.get(f"/auftrag/{aid}/stand").get_json()
    assert d["stand"] == "pruefen" and d["anteil"] == 1.0


def test_haken_weg_und_begriff_dazu(umgebung):
    c, ablage = umgebung
    aid = _auftrag_id(_hochladen(c))
    a = ablage.laden(aid)
    ohne_koch = [str(i) for i, f in enumerate(a["funde"]) if f["text"] != "Peter Koch"]
    r = c.post(f"/auftrag/{aid}/funde", data={"aktiv": ohne_koch, "neu_text": "Koch", "neu_typ": "PERSON"})
    assert r.status_code == 303
    text = c.get(f"/auftrag/{aid}/datei/0").get_data(as_text=True)
    assert "Peter [Person 2]" in text


def test_unbekannter_begriff_wird_gemeldet(umgebung):
    c, ablage = umgebung
    aid = _auftrag_id(_hochladen(c))
    alle = [str(i) for i in range(len(ablage.laden(aid)["funde"]))]
    r = c.post(f"/auftrag/{aid}/funde", data={"aktiv": alle, "neu_text": "Bernd", "neu_typ": "PERSON"},
               follow_redirects=True)
    assert "kommt in keiner Datei" in r.get_data(as_text=True)


def test_nicht_lesbare_datei_wird_erklaert(umgebung):
    c, _ = umgebung
    r = _hochladen(c, name="liste.xlsx", inhalt="x")
    assert r.status_code == 400 and ".xlsx" in r.get_data(as_text=True)


def test_ohne_datei(umgebung):
    c, _ = umgebung
    r = c.post("/auftrag", data={"typ": ["PERSON"]}, content_type="multipart/form-data")
    assert r.status_code == 400


def test_fremde_herkunft_darf_nichts_absenden(umgebung):
    c, _ = umgebung
    r = c.post("/auftrag", data={}, headers={"Origin": "https://boese.example"})
    assert r.status_code == 403


def test_zurueckuebersetzen(umgebung):
    c, _ = umgebung
    aid = _auftrag_id(_hochladen(c))
    r = c.post(f"/auftrag/{aid}/zurueck", data={"text": "Plan für [Person 2] und [Person 1]s Team"})
    assert "Plan für Peter Koch und Anna Müllers Team" in r.get_data(as_text=True)


def test_loeschen(umgebung):
    c, ablage = umgebung
    aid = _auftrag_id(_hochladen(c))
    c.post(f"/auftrag/{aid}/loeschen")
    assert c.get(f"/auftrag/{aid}").status_code == 404
    assert ablage.alle() == []


def test_unsinnige_auftragsnummer(umgebung):
    c, _ = umgebung
    assert c.get("/auftrag/..%2F..%2Fetc").status_code == 404


def test_passwortschutz(tmp_path):
    ablage = Ablage(tmp_path / "d")
    app = app_bauen(ablage, SofortWerkstatt(ablage, None),
                    lambda: {"basis": "x", "modell": "", "fehler": ""}, passwort="geheim")
    c = app.test_client()
    assert c.get("/").status_code == 401
    gut = base64.b64encode(b"egal:geheim").decode()
    assert c.get("/", headers={"Authorization": f"Basic {gut}"}).status_code == 200
    falsch = base64.b64encode(b"egal:falsch").decode()
    assert c.get("/", headers={"Authorization": f"Basic {falsch}"}).status_code == 401


@pytest.fixture
def anonym(tmp_path):
    ablage = Ablage(tmp_path / "daten")
    werkstatt = SofortWerkstatt(ablage, AnonymLLM())
    app = app_bauen(ablage, werkstatt,
                    lambda: {"basis": "http://127.0.0.1:8889/v1", "modell": "falsch-7b", "fehler": ""})
    c = app.test_client()
    aid = _auftrag_id(_hochladen(c, inhalt=ANONYM, modus="anonym"))
    return c, ablage, aid, werkstatt


def test_anonymisieren_ueber_die_oberflaeche(anonym):
    c, ablage, aid, _ = anonym
    assert ablage.laden(aid)["modus"] == "anonym"
    seite = c.get(f"/auftrag/{aid}").get_data(as_text=True)
    assert "Restrisiko" in seite and "einzige Frau im Team" in seite
    assert "zuordnung.json" not in seite and "/zurueck" not in seite
    text = c.get(f"/auftrag/{aid}/datei/0").get_data(as_text=True)
    assert "[Funktion 1]" in text and "abwesend" in text and "Frau Dr." not in text


def test_anonym_ohne_zuordnung_und_zurueckuebersetzen(anonym):
    c, _, aid, _ = anonym
    assert c.get(f"/auftrag/{aid}/zuordnung.json").status_code == 404
    assert c.get(f"/auftrag/{aid}/zurueck").status_code == 404
    assert c.post(f"/auftrag/{aid}/zurueck", data={"text": "[Person 1]"}).status_code == 404


def test_restrisiko_bericht_herunterladen_und_im_zip(anonym):
    c, _, aid, _ = anonym
    md = c.get(f"/auftrag/{aid}/restrisiko.md").get_data(as_text=True)
    assert md.startswith("# Restrisiko-Bericht") and "einzige Frau im Team" in md
    z = zipfile.ZipFile(io.BytesIO(c.get(f"/auftrag/{aid}/alle.zip").data))
    assert sorted(z.namelist()) == ["Protokoll.md", "restrisiko.md"]


def test_bericht_nach_aenderung_veraltet_und_neu(anonym):
    c, ablage, aid, werkstatt = anonym
    alle = [str(i) for i in range(len(ablage.laden(aid)["funde"]))]
    c.post(f"/auftrag/{aid}/funde", data={"aktiv": alle})
    assert "vor den letzten Änderungen" in c.get(f"/auftrag/{aid}").get_data(as_text=True)
    assert c.post(f"/auftrag/{aid}/bericht").status_code == 303
    assert werkstatt.auftraege[-1] == "bericht"
    assert "vor den letzten Änderungen" not in c.get(f"/auftrag/{aid}").get_data(as_text=True)


def test_pseudonym_hat_keinen_restrisiko_bericht(umgebung):
    c, _ = umgebung
    aid = _auftrag_id(_hochladen(c))
    assert c.get(f"/auftrag/{aid}/restrisiko.md").status_code == 404


# ------------------------------------------------------------ Texterkennung

from test_texterkennung import OCR, SCAN, _bild, _pdf, mit_tesseract  # noqa: E402


class OcrWerkstatt(SofortWerkstatt):
    def einreihen(self, auftrag_id, art="erkennen"):
        verarbeiten(self.ablage, auftrag_id, self.llm, ocr=OCR)


@mit_tesseract
def test_eingescannte_pdf_ueber_die_oberflaeche(tmp_path):
    ablage = Ablage(tmp_path / "d")
    app = app_bauen(ablage, OcrWerkstatt(ablage, FalschesLLM(personen=["Anna Mueller"])),
                    lambda: {"basis": "x", "modell": "m", "fehler": ""}, ocr=OCR)
    c = app.test_client()
    assert "eingescannte PDFs" in c.get("/").get_data(as_text=True)
    r = c.post("/auftrag", data={"dateien": (io.BytesIO(_pdf([_bild(SCAN)])), "scan.pdf"), "typ": ["PERSON"]},
               content_type="multipart/form-data")
    assert r.status_code == 303
    seite = c.get(r.headers["Location"]).get_data(as_text=True)
    assert "Texterkennung" in seite and "[Person 1]" in seite


def test_eingescannte_pdf_ohne_texterkennung_wird_beim_hochladen_erklaert(umgebung):
    c, _ = umgebung
    r = c.post("/auftrag", data={"dateien": (io.BytesIO(_pdf([_bild(SCAN)])), "scan.pdf"), "typ": ["PERSON"]},
               content_type="multipart/form-data")
    assert r.status_code == 400 and "tesseract-ocr" in r.get_data(as_text=True)


# ------------------------------------------------- Herkunft wie im Browser

def test_referrer_policy_laesst_dem_browser_seine_herkunft():
    """Bei 'no-referrer' schickt der Browser zu jedem Formular 'Origin: null' --
    dann hielt die Herkunftsprüfung jedes eigene Formular für fremd (11.11.2026)."""
    from decknamen.web import app_bauen as bauen
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        ablage = Ablage(d)
        c = bauen(ablage, SofortWerkstatt(ablage, None), lambda: {"basis": "x", "modell": "", "fehler": ""}).test_client()
        r = c.get("/")
        assert r.headers["Referrer-Policy"] == "same-origin"
        assert 'content="no-referrer"' not in r.get_data(as_text=True)


def test_formular_von_der_eigenen_seite_geht_durch(umgebung):
    c, _ = umgebung
    r = _hochladen(c, headers={"Origin": "http://localhost", "Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 303


@pytest.mark.parametrize("kopf", [{"Origin": "null"}, {"Sec-Fetch-Site": "cross-site"},
                                  {"Origin": "http://localhost", "Sec-Fetch-Site": "same-site"}])
def test_formular_von_fremder_herkunft_wird_abgewiesen(umgebung, kopf):
    c, _ = umgebung
    assert _hochladen(c, headers=kopf).status_code == 403
