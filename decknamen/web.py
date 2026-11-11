"""Die Weboberfläche: hochladen, prüfen, herunterladen.

Alles, was die Seite braucht, liefert sie selbst aus. Die Content Security
Policy verbietet dem Browser jede Verbindung nach außen -- auch eine
eingeschleuste Zeile könnte nichts nachladen oder wegschicken.
"""
from __future__ import annotations

import hmac
import io
import json
import time
import urllib.parse
import zipfile

from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, url_for
from markupsafe import Markup, escape

from . import auftraege, kern
from .kern import TYPEN
from .lesen import BILDER, ENDUNGEN, NichtLesbar, braucht_ocr, lesen

QUELLEN = {"regel": "Muster", "llm": "Modell", "nachkontrolle": "Nachkontrolle", "manuell": "von Hand"}

# Was beim Hochladen vorausgewählt ist. Firmen und Orte sind oft gewollter
# Inhalt (der eigene Firmenname, der Standort) -- die wählt man bewusst dazu.
VORGEWAEHLT = {"PERSON", "EMAIL", "TELEFON", "ADRESSE", "IBAN", "KENNUNG"}
ARTEN = [
    ("PERSON", "Namen von Personen", "auch Vornamen, Nachnamen und Kürzel"),
    ("EMAIL", "E-Mail-Adressen", ""),
    ("TELEFON", "Telefonnummern", ""),
    ("ADRESSE", "Anschriften", "Straße, Hausnummer, Ort"),
    ("IBAN", "Bankverbindungen", ""),
    ("KENNUNG", "Kennungen", "Personal-, Kunden-, Ausweisnummern"),
    ("FIRMA", "Firmen und Organisationen", "Kunden, Lieferanten, Partner"),
    ("ORT", "Orte", "Städte, Standorte"),
]
# Kommen beim Anonymisieren immer dazu (zum Ergänzen von Hand wählbar).
KONTEXT_ARTEN = [
    ("FUNKTION", "Funktionen und Rollen", "„Leiterin Vertrieb Süd“"),
    ("EINHEIT", "Abteilungen und Teams", ""),
    ("BESONDERS", "Besondere Angaben", "Gesundheit, Religion, Abmahnungen …"),
    ("DATUM", "Datumsangaben", "werden auf Anfang, Mitte oder Ende des Monats verallgemeinert"),
]

CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")


def vorschau(text: str, z: kern.Zuordnung, modus: str) -> Markup:
    typ_von = {g.platzhalter: g.typ for g in z.gruppen}
    ersatz = {g.platzhalter for g in z.gruppen if g.ersatz}
    aus, pos = [], 0
    for anfang, ende, form, ph in kern.stellen(text, z):
        aus.append(escape(text[pos:anfang]))
        klasse = "t-" + typ_von.get(ph, "SONSTIGES").lower() + (" ersatz" if ph in ersatz else "")
        if modus == "original":
            aus.append(Markup('<mark class="fund {}" title="{}">{}</mark>').format(klasse, ph, form))
        else:
            aus.append(Markup('<mark class="band {}" title="{}">{}</mark>').format(klasse, form, ph))
        pos = ende
    aus.append(escape(text[pos:]))
    return Markup("").join(aus)


def _herunterladen(daten: bytes, name: str, art: str):
    return send_file(io.BytesIO(daten), mimetype=art, as_attachment=True, download_name=name)


def app_bauen(ablage, werkstatt, llm_lage, passwort: str | None = None,
              max_mb: int = 50, aufbewahrung_tage: float = 7, ocr=None) -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = max_mb * 1024 * 1024
    lage_merker = {"zeit": 0.0, "wert": None}

    def lage():
        if time.time() - lage_merker["zeit"] > 20:
            lage_merker.update(zeit=time.time(), wert=llm_lage())
        return lage_merker["wert"]

    @app.context_processor
    def ueberall():
        return {"lage": lage(), "TYPEN": TYPEN}

    @app.before_request
    def schutz():
        if passwort:
            a = request.authorization
            if not a or not hmac.compare_digest((a.password or "").encode(), passwort.encode()):
                return ("Anmeldung nötig", 401, {"WWW-Authenticate": 'Basic realm="decknamen"'})
        if request.method == "POST":
            herkunft = request.headers.get("Origin") or request.headers.get("Referer")
            if herkunft and urllib.parse.urlsplit(herkunft).netloc != request.host:
                abort(403)

    @app.after_request
    def kopf(r):
        r.headers["Content-Security-Policy"] = CSP
        r.headers["X-Content-Type-Options"] = "nosniff"
        r.headers["Referrer-Policy"] = "no-referrer"
        r.headers["Cache-Control"] = "no-store"
        return r

    def laden(auftrag_id):
        try:
            return ablage.laden(auftrag_id)
        except KeyError:
            abort(404)

    # ---------------------------------------------------------- Startseite

    def startseite(fehler=None, code=200):
        mit_ocr = bool(ocr and ocr.verfuegbar)
        endungen = [e for e in ENDUNGEN if mit_ocr or e not in BILDER]
        return render_template("start.html", auftraege=ablage.alle(), arten=ARTEN,
                               kontext_arten=KONTEXT_ARTEN, mit_ocr=mit_ocr,
                               accept=",".join(endungen),
                               vorgewaehlt=VORGEWAEHLT, endungen=", ".join(ENDUNGEN),
                               fehler=fehler or [], tage=aufbewahrung_tage), code

    @app.get("/")
    def start():
        return startseite()

    @app.post("/auftrag")
    def neu():
        dateien = [f for f in request.files.getlist("dateien") if f and f.filename]
        if not dateien:
            return startseite(["Bitte mindestens eine Datei auswählen."], 400)
        typen = [t for t in request.form.getlist("typ") if t in TYPEN]
        if not typen:
            return startseite(["Bitte mindestens eine Art von Angaben auswählen."], 400)
        gelesen, fehler = [], []
        for f in dateien:
            name = f.filename.replace("\\", "/").rsplit("/", 1)[-1]
            daten = f.read()
            try:
                if ocr is not None and ocr.verfuegbar and braucht_ocr(name, daten):
                    gelesen.append((name, None, daten))      # Texterkennung im Hintergrund
                else:
                    gelesen.append((name, lesen(name, daten)))
            except NichtLesbar as e:
                fehler.append(f"{name}: {e}")
        if fehler:
            return startseite(fehler, 400)
        modus = request.form.get("modus", "pseudonym")
        if modus not in auftraege.MODI:
            modus = "pseudonym"
        a = ablage.neu(gelesen, typen, bool(request.form.get("nachkontrolle")), modus=modus)
        werkstatt.einreihen(a["id"])
        return redirect(url_for("auftrag", auftrag_id=a["id"]), 303)

    # ------------------------------------------------------------- Auftrag

    @app.get("/auftrag/<auftrag_id>")
    def auftrag(auftrag_id):
        a = laden(auftrag_id)
        if a["stand"] != "pruefen":
            return render_template("lauf.html", a=a)
        texte = ablage.texte(a)
        z = auftraege.zuordnung_von(ablage, a)
        zahl = kern.zaehlen(texte, z)
        index = {(f["text"], f["typ"]): i for i, f in enumerate(a["funde"])}
        gruppen = []
        for g in z.gruppen:
            formen = []
            for form in g.formen:
                i = index.get((form, g.typ))
                if i is None:
                    continue
                f = a["funde"][i]
                formen.append({"i": i, "text": form, "zahl": zahl.get(form, 0),
                               "quelle": QUELLEN.get(f["quelle"], f["quelle"])})
            gruppen.append({"ph": g.platzhalter, "typ": g.typ, "anzeige": g.anzeige, "formen": formen,
                            "ersatz": g.ersatz})
        aus = {i for g in gruppen for f in g["formen"] for i in [f["i"]]}
        uebrige = [{"i": i, "text": f["text"], "typ": f["typ"],
                    "quelle": QUELLEN.get(f["quelle"], f["quelle"])}
                   for i, f in enumerate(a["funde"]) if i not in aus and not f["aktiv"]]
        namen = [n for n, _ in auftraege.ergebnisse(ablage, a)]
        nr = min(max(request.args.get("datei", 0, type=int), 0), len(texte) - 1)
        personen = [g for g in gruppen if g["typ"] == "PERSON"]
        anonym = a["modus"] == "anonym"
        bericht = a.get("bericht")
        return render_template(
            "pruefen.html", a=a, gruppen=gruppen, uebrige=uebrige, namen=namen, nr=nr,
            ocr_seiten=a["dateien"][nr].get("ocr_seiten") or [],
            ergebnis=vorschau(texte[nr], z, "ergebnis"), original=vorschau(texte[nr], z, "original"),
            ersetzungen=sum(zahl.values()), personen=personen,
            arten=ARTEN + (KONTEXT_ARTEN if anonym else []), anonym=anonym, bericht=bericht,
            bericht_veraltet=bool(bericht) and bericht.get("stand") != a["funde_stand"],
            meldung=request.args.get("meldung", ""))

    @app.get("/auftrag/<auftrag_id>/stand")
    def stand(auftrag_id):
        a = laden(auftrag_id)
        return jsonify({k: a[k] for k in ("stand", "schritt", "anteil", "fehler")})

    @app.post("/auftrag/<auftrag_id>/funde")
    def funde(auftrag_id):
        a = laden(auftrag_id)
        nr = request.form.get("datei", 0, type=int)
        aktiv = {int(x) for x in request.form.getlist("aktiv") if x.isdigit()}
        auftraege.funde_setzen(ablage, a, aktiv)
        meldung = ""
        neu_text = request.form.get("neu_text", "").strip()
        if neu_text:
            try:
                auftraege.fund_hinzufuegen(ablage, a, neu_text, request.form.get("neu_typ", "PERSON"),
                                           request.form.get("neu_person", ""),
                                           request.form.get("neu_ersatz", ""))
            except auftraege.NichtGefunden as e:
                meldung = str(e)
        return redirect(url_for("auftrag", auftrag_id=auftrag_id, datei=nr, meldung=meldung or None)
                        + "#funde", 303)

    @app.post("/auftrag/<auftrag_id>/neu")
    def nochmal(auftrag_id):
        laden(auftrag_id)
        werkstatt.einreihen(auftrag_id)
        return redirect(url_for("auftrag", auftrag_id=auftrag_id), 303)

    @app.post("/auftrag/<auftrag_id>/bericht")
    def bericht_neu(auftrag_id):
        a = laden(auftrag_id)
        if a["modus"] != "anonym" or a["stand"] != "pruefen":
            abort(404)
        werkstatt.einreihen(auftrag_id, "bericht")
        return redirect(url_for("auftrag", auftrag_id=auftrag_id), 303)

    @app.post("/auftrag/<auftrag_id>/loeschen")
    def loeschen(auftrag_id):
        laden(auftrag_id)
        ablage.loeschen(auftrag_id)
        return redirect(url_for("start"), 303)

    # ------------------------------------------------------------- Ausgabe

    @app.get("/auftrag/<auftrag_id>/datei/<int:nr>")
    def datei(auftrag_id, nr):
        a = laden(auftrag_id)
        if a["stand"] != "pruefen":
            abort(409)
        aus = auftraege.ergebnisse(ablage, a)
        if not 0 <= nr < len(aus):
            abort(404)
        name, text = aus[nr]
        return _herunterladen(text.encode(), name, "text/markdown; charset=utf-8")

    @app.get("/auftrag/<auftrag_id>/alle.zip")
    def alle(auftrag_id):
        a = laden(auftrag_id)
        if a["stand"] != "pruefen":
            abort(409)
        puffer = io.BytesIO()
        with zipfile.ZipFile(puffer, "w", zipfile.ZIP_DEFLATED) as z:
            for name, text in auftraege.ergebnisse(ablage, a):
                z.writestr(name, text)
            if a["modus"] == "anonym" and a.get("bericht"):
                z.writestr("restrisiko.md", auftraege.bericht_markdown(ablage, a))
        return _herunterladen(puffer.getvalue(), f"pseudonymisiert-{auftrag_id}.zip", "application/zip")

    @app.get("/auftrag/<auftrag_id>/restrisiko.md")
    def restrisiko(auftrag_id):
        a = laden(auftrag_id)
        if a["modus"] != "anonym" or not a.get("bericht") or a["stand"] != "pruefen":
            abort(404)
        return _herunterladen(auftraege.bericht_markdown(ablage, a).encode(),
                              f"restrisiko-{auftrag_id}.md", "text/markdown; charset=utf-8")

    @app.get("/auftrag/<auftrag_id>/zuordnung.json")
    def zuordnung(auftrag_id):
        a = laden(auftrag_id)
        if a["modus"] == "anonym":
            abort(404)
        d = auftraege.zuordnung_export(a, ablage)
        return _herunterladen(json.dumps(d, ensure_ascii=False, indent=2).encode(),
                              f"zuordnung-{auftrag_id}.json", "application/json")

    @app.route("/auftrag/<auftrag_id>/zurueck", methods=["GET", "POST"])
    def zurueck(auftrag_id):
        a = laden(auftrag_id)
        if a["modus"] == "anonym":
            abort(404)
        eingabe = request.form.get("text", "")
        ausgabe = kern.zurueck(eingabe, auftraege.zuordnung_von(ablage, a)) if eingabe else ""
        return render_template("zurueck.html", a=a, eingabe=eingabe, ausgabe=ausgabe)

    @app.errorhandler(413)
    def zu_gross(_):
        return startseite([f"Die Dateien sind zusammen größer als {max_mb} MB."], 413)

    @app.template_filter("zeit")
    def zeit(t):
        return time.strftime("%d.%m.%Y, %H:%M", time.localtime(t))

    return app
