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

_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")


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

    def neu(self, dateien: list[tuple[str, str]], typen: list[str], nachkontrolle: bool) -> dict:
        auftrag_id = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
        ordner = self._pfad(auftrag_id)
        _ordner(ordner)
        _ordner(ordner / "texte")
        for i, (_, text) in enumerate(dateien):
            _schreiben(ordner / "texte" / f"{i}.md", text)
        a = {"id": auftrag_id, "erstellt": time.time(),
             "dateien": [{"name": n, "zeichen": len(t)} for n, t in dateien],
             "typen": [t for t in TYPEN if t in typen], "nachkontrolle": bool(nachkontrolle),
             "stand": "wartet", "schritt": "In der Warteschlange", "anteil": 0.0,
             "fehler": "", "modell": "", "funde": [], "dauer_s": None}
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
        return json.loads(p.read_text(encoding="utf-8"))

    def texte(self, a: dict) -> list[str]:
        o = self._pfad(a["id"]) / "texte"
        return [(o / f"{i}.md").read_text(encoding="utf-8") for i in range(len(a["dateien"]))]

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
        grenze = time.time() - tage * 86400
        alt = [a for a in self.alle() if a["erstellt"] < grenze and a["stand"] not in ("laeuft",)]
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


def verarbeiten(ablage: Ablage, auftrag_id: str, llm, belegung=None, laenge: int = 4000) -> None:
    a = ablage.laden(auftrag_id)
    texte = ablage.texte(a)
    typen = set(a["typen"])
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

    try:
        melden(stand="laeuft", schritt="Feste Muster: E-Mail, Telefon, IBAN", anteil=0.0, fehler="")
        for t in texte:
            dazu(regeln.finde(t, typen))

        if llm is not None and typen & set(erkennen.BESCHREIBUNG):
            # Im Zweck stehen nie Dateinamen: die enthalten oft selbst Namen.
            zweck = f"Pseudonymisierung ({len(texte)} Datei{'en' if len(texte) != 1 else ''})"
            halten = belegung.halten(zweck, warten=lambda h: melden(stand="wartet", schritt=h)) \
                if belegung else contextlib.nullcontext()
            with halten:
                melden(stand="laeuft", schritt="Sprachmodell wird gefragt", modell=llm.modell_name())
                modelltypen = typen & set(erkennen.BESCHREIBUNG)

                def frage(system, nutzer):
                    return llm.chat(system, nutzer, schema=erkennen.schema(modelltypen))

                laeufe = 2 if a["nachkontrolle"] else 1
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
                    z = kern.zuordnen(list(funde.values()), texte)
                    durchgang([kern.ersetzen(t, z) for t in texte], True)

        bereinigt: dict[tuple[str, str], Fund] = {}
        for f in kern._genitiv_abstreifen(list(funde.values())):
            bereinigt.setdefault((f.text, f.typ), f)
        melden(stand="pruefen", schritt="", anteil=1.0,
               funde=[f.als_dict() for f in bereinigt.values()],
               dauer_s=round(time.time() - beginn))
    except Exception as e:
        melden(stand="fehler", fehler=str(e)[:500] or type(e).__name__, schritt="",
               funde=[f.als_dict() for f in funde.values()])


class Werkstatt:
    """Arbeitet Aufträge nacheinander ab -- das Modell ist ohnehin nur eins."""

    def __init__(self, ablage: Ablage, llm_bauen, belegung=None, laenge: int = 4000,
                 aufbewahrung_tage: float = 7):
        self.ablage, self.llm_bauen, self.belegung = ablage, llm_bauen, belegung
        self.laenge, self.tage = laenge, aufbewahrung_tage
        self.schlange: queue.Queue[str] = queue.Queue()
        for a in reversed(ablage.alle()):            # nach einem Neustart weitermachen
            if a["stand"] in ("wartet", "laeuft"):
                self.einreihen(a["id"])
        threading.Thread(target=self._arbeiten, daemon=True).start()

    def einreihen(self, auftrag_id: str) -> None:
        a = self.ablage.laden(auftrag_id)
        a.update(stand="wartet", schritt="In der Warteschlange", anteil=0.0, fehler="")
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
                verarbeiten(self.ablage, auftrag_id, llm, self.belegung, self.laenge)
            except Exception:
                traceback.print_exc()


# ------------------------------------------------------------ Prüfen, Ausgabe

def _funde(a: dict) -> list[Fund]:
    return [Fund.aus_dict(d) for d in a["funde"]]


def zuordnung_von(ablage: Ablage, a: dict) -> kern.Zuordnung:
    return kern.zuordnen(_funde(a), ablage.texte(a))


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
    ablage.speichern(a)


def fund_hinzufuegen(ablage: Ablage, a: dict, text: str, typ: str, person: str) -> None:
    text, person = text.strip(), person.strip()
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
            break
    else:
        a["funde"].append(Fund(text, typ, person=person if typ == "PERSON" else "",
                               quelle="manuell").als_dict())
    ablage.speichern(a)


def zuordnung_export(a: dict, ablage: Ablage) -> dict:
    z = zuordnung_von(ablage, a)
    return {g.platzhalter: {"typ": g.typ, "steht_fuer": g.anzeige, "schreibweisen": g.formen}
            for g in z.gruppen}
