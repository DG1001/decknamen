"""Aufträge: ablegen, abarbeiten, prüfen, ausgeben.

Ein Auftrag umfasst eine oder mehrere Dateien. Abgelegt wird nur der
herausgelöste Text (nicht die hochgeladene Datei) und die Liste der Funde.
Die Ablage ist nur für den eigenen Benutzer lesbar (0700/0600) und wird
nach einer einstellbaren Zahl von Tagen geräumt -- sie enthält ja genau die
Daten, um die es geht.
"""
from __future__ import annotations

import contextlib
import json
import os
import pathlib
import queue
import re
import secrets
import shutil
import threading
import time
import traceback

from . import erkennen, kern, regeln
from .kern import Fund, TYPEN
from .lesen import lesen_mit_info

_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")

# Beim Anonymisieren kommt immer dazu, was Personen auch ohne Namen erkennbar macht.
KONTEXT_TYPEN = ["FUNKTION", "EINHEIT", "BESONDERS", "DATUM"]
MODI = ("pseudonym", "anonym")
ANONYM_HOECHSTENS_TAGE = 1


class NichtGefunden(Exception):
    pass


def _schreiben(pfad: pathlib.Path, inhalt: str) -> None:
    tmp = pfad.with_name(pfad.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(inhalt)
    os.replace(tmp, pfad)


def _ordner(pfad: pathlib.Path) -> None:
    pfad.mkdir(parents=True, exist_ok=True)
    os.chmod(pfad, 0o700)


class Ablage:
    def __init__(self, wurzel):
        self.wurzel = pathlib.Path(wurzel)
        _ordner(self.wurzel)
        self._sperre = threading.Lock()

    def _pfad(self, auftrag_id: str) -> pathlib.Path:
        if not _ID.match(auftrag_id or ""):
            raise KeyError(auftrag_id)
        return self.wurzel / auftrag_id

    def neu(self, dateien: list[tuple], typen: list[str], nachkontrolle: bool,
            modus: str = "pseudonym") -> dict:
        """dateien: (Name, Text) oder (Name, None, Rohdaten) für die spätere Texterkennung."""
        if modus not in MODI:
            raise ValueError(modus)
        typen = [t for t in typen if t not in KONTEXT_TYPEN]
        if modus == "anonym":
            typen = typen + KONTEXT_TYPEN
        auftrag_id = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
        ordner = self._pfad(auftrag_id)
        _ordner(ordner)
        _ordner(ordner / "texte")
        _ordner(ordner / "roh")
        eintraege = []
        for i, (name, text, *roh) in enumerate(dateien):
            if text is None:
                # Bis zur Texterkennung liegt die Datei selbst hier, danach nicht mehr.
                pfad = ordner / "roh" / f"{i}{pathlib.Path(name).suffix.lower()}"
                fd = os.open(pfad, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "wb") as f:
                    f.write(roh[0])
                eintraege.append({"name": name, "zeichen": 0, "ocr_seiten": [], "ocr_offen": True})
            else:
                _schreiben(ordner / "texte" / f"{i}.md", text)
                eintraege.append({"name": name, "zeichen": len(text), "ocr_seiten": []})
        a = {"id": auftrag_id, "erstellt": time.time(),
             "dateien": eintraege,
             "typen": [t for t in TYPEN if t in typen], "nachkontrolle": bool(nachkontrolle),
             "modus": modus, "art": "erkennen",
             "stand": "wartet", "schritt": "In der Warteschlange", "anteil": 0.0,
             "fehler": "", "modell": "", "funde": [], "funde_stand": 0, "bericht": None,
             "bericht_fehler": "", "dauer_s": None}
        self.speichern(a)
        return a

    def speichern(self, a: dict) -> None:
        with self._sperre:
            _schreiben(self._pfad(a["id"]) / "auftrag.json",
                       json.dumps(a, ensure_ascii=False, indent=1))

    def laden(self, auftrag_id: str) -> dict:
        p = self._pfad(auftrag_id) / "auftrag.json"
        if not p.is_file():
            raise KeyError(auftrag_id)
        a = json.loads(p.read_text(encoding="utf-8"))
        for k, v in (("modus", "pseudonym"), ("art", "erkennen"), ("funde_stand", 0),
                     ("bericht", None), ("bericht_fehler", "")):
            a.setdefault(k, v)
        return a

    def texte(self, a: dict) -> list[str]:
        o = self._pfad(a["id"]) / "texte"
        return [(o / f"{i}.md").read_text(encoding="utf-8") if (o / f"{i}.md").is_file() else ""
                for i in range(len(a["dateien"]))]

    def roh(self, a: dict, i: int) -> pathlib.Path | None:
        o = self._pfad(a["id"]) / "roh"
        return next(iter(o.glob(f"{i}.*")), None) if o.is_dir() else None

    def text_setzen(self, a: dict, i: int, text: str) -> None:
        _schreiben(self._pfad(a["id"]) / "texte" / f"{i}.md", text)

    def alle(self) -> list[dict]:
        aus = []
        for p in self.wurzel.iterdir():
            if _ID.match(p.name):
                try:
                    aus.append(self.laden(p.name))
                except (KeyError, json.JSONDecodeError):
                    continue
        return sorted(aus, key=lambda a: a["erstellt"], reverse=True)

    def loeschen(self, auftrag_id: str) -> None:
        shutil.rmtree(self._pfad(auftrag_id), ignore_errors=True)

    def aufraeumen(self, tage: float) -> int:
        def grenze(a):
            t = min(tage, ANONYM_HOECHSTENS_TAGE) if a["modus"] == "anonym" else tage
            return time.time() - t * 86400
        alt = [a for a in self.alle() if a["erstellt"] < grenze(a) and a["stand"] not in ("laeuft",)]
        for a in alt:
            self.loeschen(a["id"])
        return len(alt)


# ------------------------------------------------------------- Abarbeiten

def _personen(funde) -> list[str]:
    namen = []
    for f in funde:
        if f.typ == "PERSON" and f.aktiv:
            n = f.person or f.text
            if len(n.split()) >= 2 and n not in namen:
                namen.append(n)
    return namen


def _texterkennung(ablage: Ablage, a: dict, ocr, melden) -> None:
    """Zurückgestellte Dateien lesen; die Rohdatei wird danach gelöscht."""
    offen = [i for i, d in enumerate(a["dateien"]) if d.get("ocr_offen")]
    for k, i in enumerate(offen):
        d = a["dateien"][i]
        pfad = ablage.roh(a, i)
        if pfad is None:
            raise RuntimeError(f"Die hochgeladene Datei {k + 1} fehlt, bitte neu hochladen.")

        def fort(s, n, k=k):
            melden(stand="laeuft", schritt=f"Texterkennung: Datei {k + 1} von {len(offen)}, "
                                           f"Seite {min(s + 1, n)} von {n}")
        text, seiten = lesen_mit_info(d["name"], pfad.read_bytes(), ocr, fort)
        ablage.text_setzen(a, i, text)
        d.update(zeichen=len(text), ocr_seiten=seiten, ocr_offen=False)
        melden()
        pfad.unlink()


def verarbeiten(ablage: Ablage, auftrag_id: str, llm, belegung=None, laenge: int = 4000,
                ocr=None) -> None:
    a = ablage.laden(auftrag_id)
    typen = set(a["typen"])
    anonym = a["modus"] == "anonym"
    beginn = time.time()

    def melden(**k):
        a.update(k)
        ablage.speichern(a)

    funde: dict[tuple[str, str], Fund] = {}

    def dazu(fs):
        for f in fs:
            alt = funde.get((f.text, f.typ))
            if alt is None:
                funde[(f.text, f.typ)] = f
            elif f.person and not alt.person:
                alt.person = f.person

    def abschliessen():
        a["funde"] = _bereinigt(funde.values())
        a["funde_stand"] += 1                  # der Restrisiko-Bericht merkt sich diesen Stand

    try:
        abgeschlossen = False
        melden(stand="laeuft", anteil=0.0, fehler="")
        _texterkennung(ablage, a, ocr, melden)
        texte = ablage.texte(a)
        melden(schritt="Feste Muster: E-Mail, Telefon, IBAN")
        for t in texte:
            dazu(regeln.finde(t, typen))

        if llm is not None and typen & set(erkennen.BESCHREIBUNG):
            # Im Zweck stehen nie Dateinamen: die enthalten oft selbst Namen.
            zweck = (f"{'Anonymisierung' if anonym else 'Pseudonymisierung'} "
                     f"({len(texte)} Datei{'en' if len(texte) != 1 else ''})")
            halten = belegung.halten(zweck, warten=lambda h: melden(stand="wartet", schritt=h)) \
                if belegung else contextlib.nullcontext()
            with halten:
                melden(stand="laeuft", schritt="Sprachmodell wird gefragt", modell=llm.modell_name())
                modelltypen = typen & set(erkennen.BESCHREIBUNG)

                def frage(system, nutzer):
                    return llm.chat(system, nutzer, schema=erkennen.schema(modelltypen))

                laeufe = (2 if a["nachkontrolle"] else 1) + (1 if anonym else 0)
                je_datei = [len(erkennen.abschnitte(t, laenge)) for t in texte]
                gesamt = max(1, sum(je_datei) * laeufe)
                erledigt = 0

                def durchgang(quellen, nachkontrolle):
                    nonlocal erledigt
                    name = "Nachkontrolle" if nachkontrolle else "Erkennung"
                    for k, t in enumerate(quellen):
                        def fort(i, n, k=k):
                            melden(schritt=f"{name}: Datei {k + 1} von {len(quellen)}, "
                                           f"Abschnitt {min(i + 1, n)} von {n}",
                                   anteil=min(1.0, (erledigt + i) / gesamt))
                        dazu(erkennen.finde_llm(
                            t, modelltypen, frage, laenge, nachkontrolle=nachkontrolle,
                            fortschritt=fort,
                            bekannte=_personen(funde.values())))
                        erledigt += je_datei[k]

                durchgang(texte, False)
                if a["nachkontrolle"]:
                    z = kern.zuordnen(list(funde.values()), texte, anrede_weg=anonym)
                    durchgang([kern.ersetzen(t, z) for t in texte], True)
                abschliessen()
                abgeschlossen = True
                if anonym:
                    _bericht(a, texte, llm, laenge, melden,
                             lambda i: min(1.0, (erledigt + i) / gesamt))

        if not abgeschlossen:
            abschliessen()
        melden(stand="pruefen", schritt="", anteil=1.0, dauer_s=round(time.time() - beginn))
    except Exception as e:
        melden(stand="fehler", fehler=str(e)[:500] or type(e).__name__, schritt="",
               funde=[f.als_dict() for f in funde.values()])


def _bereinigt(funde) -> list[dict]:
    aus: dict[tuple[str, str], Fund] = {}
    for f in kern._genitiv_abstreifen(list(funde)):
        aus.setdefault((f.text, f.typ), f)
    return [f.als_dict() for f in aus.values()]


def _bericht(a: dict, texte: list[str], llm, laenge: int, melden, anteil=lambda i: 0.0) -> None:
    """Restrisiko je Datei, auf dem fertig anonymisierten Text."""
    z = kern.zuordnen(_funde(a), texte, anrede_weg=True)
    hinweise, schon = [], 0

    def frage(system, nutzer):
        return llm.chat(system, nutzer, schema=erkennen.SCHEMA_BERICHT)

    for k, t in enumerate(texte):
        def fort(i, n, k=k):
            melden(schritt=f"Restrisiko: Datei {k + 1} von {len(texte)}, Abschnitt {min(i + 1, n)} von {n}",
                   anteil=anteil(schon + i))
        for h in erkennen.restrisiko(kern.ersetzen(t, z), frage, laenge, fort):
            hinweise.append({"datei": k, **h})
        schon += len(erkennen.abschnitte(t, laenge))
    a["bericht"] = {"erstellt": time.time(), "modell": llm.modell_name(),
                    "stand": a["funde_stand"], "hinweise": hinweise}
    a["bericht_fehler"] = ""


def bericht_erstellen(ablage: Ablage, auftrag_id: str, llm, belegung=None, laenge: int = 4000) -> None:
    """Nur den Restrisiko-Bericht neu erstellen, die Funde bleiben, wie sie sind."""
    a = ablage.laden(auftrag_id)

    def melden(**k):
        a.update(k)
        ablage.speichern(a)

    try:
        melden(stand="laeuft", schritt="Restrisiko wird geprüft", anteil=0.0, bericht_fehler="")
        halten = belegung.halten("Restrisiko-Prüfung", warten=lambda h: melden(stand="wartet", schritt=h)) \
            if belegung else contextlib.nullcontext()
        with halten:
            melden(stand="laeuft")
            texte = ablage.texte(a)
            gesamt = max(1, sum(len(erkennen.abschnitte(t, laenge)) for t in texte))
            _bericht(a, texte, llm, laenge, melden, lambda i: min(1.0, i / gesamt))
    except Exception as e:
        a["bericht_fehler"] = str(e)[:500] or type(e).__name__
    melden(stand="pruefen", schritt="", anteil=1.0, art="erkennen")


class Werkstatt:
    """Arbeitet Aufträge nacheinander ab -- das Modell ist ohnehin nur eins."""

    def __init__(self, ablage: Ablage, llm_bauen, belegung=None, laenge: int = 4000,
                 aufbewahrung_tage: float = 7, ocr=None):
        self.ablage, self.llm_bauen, self.belegung, self.ocr = ablage, llm_bauen, belegung, ocr
        self.laenge, self.tage = laenge, aufbewahrung_tage
        self.schlange: queue.Queue[str] = queue.Queue()
        for a in reversed(ablage.alle()):            # nach einem Neustart weitermachen
            if a["stand"] in ("wartet", "laeuft"):
                self.einreihen(a["id"], a["art"])
        threading.Thread(target=self._arbeiten, daemon=True).start()

    def einreihen(self, auftrag_id: str, art: str = "erkennen") -> None:
        a = self.ablage.laden(auftrag_id)
        a.update(stand="wartet", schritt="In der Warteschlange", anteil=0.0, fehler="", art=art)
        self.ablage.speichern(a)
        self.schlange.put(auftrag_id)

    def _arbeiten(self) -> None:
        while True:
            try:
                auftrag_id = self.schlange.get(timeout=3600)
            except queue.Empty:
                self.ablage.aufraeumen(self.tage)
                continue
            try:
                llm = self.llm_bauen()
            except Exception as e:
                a = self.ablage.laden(auftrag_id)
                a.update(stand="fehler", fehler=str(e)[:500])
                self.ablage.speichern(a)
                continue
            try:
                if self.ablage.laden(auftrag_id)["art"] == "bericht":
                    bericht_erstellen(self.ablage, auftrag_id, llm, self.belegung, self.laenge)
                else:
                    verarbeiten(self.ablage, auftrag_id, llm, self.belegung, self.laenge, self.ocr)
            except Exception:
                traceback.print_exc()


# ------------------------------------------------------------ Prüfen, Ausgabe

def _funde(a: dict) -> list[Fund]:
    return [Fund.aus_dict(d) for d in a["funde"]]


def zuordnung_von(ablage: Ablage, a: dict) -> kern.Zuordnung:
    return kern.zuordnen(_funde(a), ablage.texte(a), anrede_weg=a["modus"] == "anonym")


def _dateiname(name: str, z: kern.Zuordnung) -> str:
    stamm = pathlib.Path(name).stem
    # Unterstriche trennen in Dateinamen Wörter ("Protokoll_Anna_Müller"), gelten
    # für die Wortgrenze aber als Buchstabe. Gleich lang ersetzen, Stellen übernehmen.
    offen = stamm.replace("_", " ")
    aus, pos = [], 0
    for anfang, ende, _, ph in kern.stellen(offen, z):
        aus += [stamm[pos:anfang], ph]
        pos = ende
    aus.append(stamm[pos:])
    return "".join(aus) + ".md"


def ergebnisse(ablage: Ablage, a: dict) -> list[tuple[str, str]]:
    z = zuordnung_von(ablage, a)
    aus, vergeben = [], set()
    for d, t in zip(a["dateien"], ablage.texte(a)):
        name = _dateiname(d["name"], z)
        stamm, n = name[:-3], 2
        while name in vergeben:
            name, n = f"{stamm}-{n}.md", n + 1
        vergeben.add(name)
        aus.append((name, kern.ersetzen(t, z)))
    return aus


def funde_setzen(ablage: Ablage, a: dict, aktiv: set[int]) -> None:
    for i, f in enumerate(a["funde"]):
        f["aktiv"] = i in aktiv
    a["funde_stand"] += 1
    ablage.speichern(a)


def fund_hinzufuegen(ablage: Ablage, a: dict, text: str, typ: str, person: str,
                     ersatz: str = "") -> None:
    text, person, ersatz = kern.einheitlich(text), person.strip(), ersatz.strip()
    if typ not in kern.ERSATZ_TYPEN:
        ersatz = ""
    if typ not in TYPEN or not text:
        raise NichtGefunden("Bitte einen Begriff und eine Art angeben.")
    if kern.PLATZHALTER.search(text):
        raise NichtGefunden("Platzhalter lassen sich nicht noch einmal ersetzen.")
    if text not in kern._erstes_auftreten(ablage.texte(a), [text]):
        raise NichtGefunden(f"„{text}“ kommt in keiner Datei als ganzes Wort vor.")
    for f in a["funde"]:
        if f["text"] == text and f["typ"] == typ:
            f["aktiv"] = True
            if person:
                f["person"] = person
            if ersatz:
                f["ersatz"] = ersatz
            break
    else:
        a["funde"].append(Fund(text, typ, person=person if typ == "PERSON" else "",
                               quelle="manuell", ersatz=ersatz).als_dict())
    a["funde_stand"] += 1
    ablage.speichern(a)


def zuordnung_export(a: dict, ablage: Ablage) -> dict:
    if a["modus"] == "anonym":
        raise PermissionError("Beim Anonymisieren gibt es keine Zuordnung.")
    z = zuordnung_von(ablage, a)
    return {g.platzhalter: {"typ": g.typ, "steht_fuer": g.anzeige, "schreibweisen": g.formen}
            for g in z.gruppen}


def bericht_markdown(ablage: Ablage, a: dict) -> str:
    """Der Restrisiko-Bericht zum Mitgeben. Enthält nur anonymisierten Text."""
    b = a.get("bericht") or {}
    z = zuordnung_von(ablage, a)
    namen = [n for n, _ in ergebnisse(ablage, a)]
    zahl = kern.zaehlen(ablage.texte(a), z)
    platz: dict[str, int] = {}
    verallg: dict[str, int] = {}
    for g in z.gruppen:
        if g.ersatz:
            verallg[g.typ] = verallg.get(g.typ, 0) + sum(zahl.get(f, 0) for f in g.formen)
        else:
            platz[g.typ] = platz.get(g.typ, 0) + 1
    zeilen = [
        "# Restrisiko-Bericht", "",
        f"Erstellt am {time.strftime('%d.%m.%Y um %H:%M', time.localtime(b.get('erstellt', time.time())))} "
        f"mit dem lokalen Modell {b.get('modell') or a.get('modell') or 'unbekannt'}.", "",
        "Dieser Bericht ist eine automatische Prüfung durch ein Sprachmodell. Er ersetzt nicht die "
        "Prüfung durch einen Menschen und ist keine Bestätigung, dass die Texte anonym im Sinne der "
        "DSGVO sind.", "",
    ]
    if b and b.get("stand") != a["funde_stand"]:
        zeilen += ["**Achtung:** Der Bericht stammt von vor den letzten Änderungen an den Funden.", ""]
    zeilen += ["## Was ersetzt wurde", "", f"- Dateien: {', '.join(namen)}"]
    if platz:
        zeilen.append("- Platzhalter: " + ", ".join(f"{n}× {TYPEN[t]}" for t, n in platz.items()))
    if verallg:
        wort = {"DATUM": ("Datumsangabe", "Datumsangaben"),
                "BESONDERS": ("besondere Angabe", "besondere Angaben")}
        zeilen.append("- Verallgemeinert: " + ", ".join(
            f"{n} {wort[t][n != 1]}" for t, n in verallg.items()))
    zeilen += ["- Anreden und Titel vor Namen entfernt",
               f"- Keine Zuordnung abrufbar, der Auftrag wird spätestens nach "
               f"{ANONYM_HOECHSTENS_TAGE} Tag gelöscht", "", "## Hinweise des Modells", ""]
    hinweise = b.get("hinweise", [])
    if not hinweise:
        zeilen += ["Das Modell hat keine Stellen gefunden, an denen Personen erkennbar bleiben. "
                   "Das ist kein Beweis, dass es keine gibt.", ""]
    for k, name in enumerate(namen):
        eigene = [h for h in hinweise if h["datei"] == k]
        if not eigene:
            continue
        zeilen += [f"### {name}", ""]
        for i, h in enumerate(eigene, 1):
            zeilen += [f"{i}. „{h['stelle']}“" + ("" if h["gefunden"] else " (nicht wörtlich im Text)"),
                       f"   Grund: {h['grund']}", f"   Vorschlag: {h['vorschlag']}", ""]
    return "\n".join(zeilen).rstrip() + "\n"
