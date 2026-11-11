"""Start: python -m decknamen [Optionen]"""
from __future__ import annotations

import argparse
import ipaddress
import os
import pathlib
import sys

from .auftraege import Ablage, Werkstatt
from .belegung import Belegungsstelle
from .llm import LLM, LLMFehler
from .lokal import NichtLokal, pruefe_lokal
from .web import app_bauen


def _lokale_bindung(adresse: str) -> bool:
    if adresse == "localhost":
        return True
    try:
        return ipaddress.ip_address(adresse).is_loopback
    except ValueError:
        return False


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="decknamen",
                                description="Dokumente mit einem lokalen Sprachmodell pseudonymisieren.")
    umg = os.environ.get
    p.add_argument("--llm", default=umg("DECKNAMEN_LLM", "http://127.0.0.1:8000/v1"),
                   help="OpenAI-kompatibler Endpunkt des lokalen Modells (Vorgabe: %(default)s)")
    p.add_argument("--modell", default=umg("DECKNAMEN_MODELL"),
                   help="Modellname; ohne Angabe das erste, das der Endpunkt meldet")
    p.add_argument("--adresse", default=umg("DECKNAMEN_ADRESSE", "127.0.0.1"),
                   help="Adresse der Weboberfläche (Vorgabe: %(default)s)")
    p.add_argument("--port", type=int, default=int(umg("DECKNAMEN_PORT", "8096")))
    p.add_argument("--daten", default=umg("DECKNAMEN_DATEN", "daten"),
                   help="Ablage für Aufträge (Vorgabe: ./daten)")
    p.add_argument("--aufbewahrung", type=float, default=float(umg("DECKNAMEN_AUFBEWAHRUNG", "7")),
                   help="Aufträge nach so vielen Tagen löschen (Vorgabe: %(default)s)")
    p.add_argument("--passwort-datei", default=umg("DECKNAMEN_PASSWORT_DATEI"),
                   help="Datei mit dem Passwort für die Weboberfläche "
                        "(oder Umgebungsvariable DECKNAMEN_PASSWORT)")
    p.add_argument("--belegung", default=umg("DECKNAMEN_BELEGUNG"),
                   help="URL einer Belegungsstelle für ein geteiltes Modell (optional)")
    p.add_argument("--abschnitt", type=int, default=4000,
                   help="Zeichen je Anfrage an das Modell (Vorgabe: %(default)s)")
    p.add_argument("--mit-denken", action="store_true",
                   help="Denkmodus des Modells nicht abschalten (langsamer)")
    p.add_argument("--erlaube-entferntes-llm", action="store_true",
                   help="Auch nicht-lokale LLM-Adressen zulassen. Dann verlassen die Dokumente das Netz!")
    a = p.parse_args(argv)

    try:
        pruefe_lokal(a.llm, a.erlaube_entferntes_llm)
    except NichtLokal as e:
        print(f"decknamen: {e}", file=sys.stderr)
        return 2
    if a.erlaube_entferntes_llm:
        print("decknamen: WARNUNG: Entfernte LLM-Adressen sind zugelassen.", file=sys.stderr)

    passwort = umg("DECKNAMEN_PASSWORT")
    if a.passwort_datei:
        passwort = pathlib.Path(a.passwort_datei).read_text(encoding="utf-8").strip()
    if not _lokale_bindung(a.adresse) and not passwort:
        print(f"decknamen: Die Oberfläche soll unter {a.adresse} erreichbar sein, also auch für andere "
              "im Netz. Dafür bitte ein Passwort setzen (--passwort-datei oder DECKNAMEN_PASSWORT).",
              file=sys.stderr)
        return 2

    def llm_bauen():
        return LLM(a.llm, modell=a.modell, erlaube_entfernt=a.erlaube_entferntes_llm,
                   denken_aus=not a.mit_denken)

    def llm_lage():
        try:
            return {"basis": a.llm, "modell": llm_bauen().modell_name(), "fehler": ""}
        except (LLMFehler, NichtLokal) as e:
            return {"basis": a.llm, "modell": "", "fehler": str(e)}

    ablage = Ablage(a.daten)
    geraeumt = ablage.aufraeumen(a.aufbewahrung)
    belegung = Belegungsstelle(a.belegung) if a.belegung else None
    werkstatt = Werkstatt(ablage, llm_bauen, belegung, a.abschnitt, a.aufbewahrung)
    app = app_bauen(ablage, werkstatt, llm_lage, passwort=passwort, aufbewahrung_tage=a.aufbewahrung)

    lage = llm_lage()
    print(f"decknamen: Modell {lage['modell'] or '(nicht erreichbar)'} unter {a.llm}", file=sys.stderr)
    if geraeumt:
        print(f"decknamen: {geraeumt} alte Aufträge gelöscht", file=sys.stderr)
    print(f"decknamen: Oberfläche unter http://{a.adresse}:{a.port}/", file=sys.stderr)

    from werkzeug.serving import run_simple
    run_simple(a.adresse, a.port, app, threaded=True, use_reloader=False, use_debugger=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
