"""Optionale Anmeldung bei einer Belegungsstelle für das geteilte Modell.

Teilen sich mehrere Anwendungen eine GPU, führt eine Belegungsstelle Buch,
wer gerade welches Modell braucht, und verhindert, dass jemand mitten in
einem Lauf auf ein anderes Modell umschaltet. decknamen meldet sich für
die Dauer eines Auftrags exklusiv für das *gerade geladene* Modell an, hält
die Anmeldung mit einem Herzschlag und gibt sie danach frei. Ist die Stelle
nicht erreichbar, geht die Arbeit ohne sie weiter.

Erwartete Schnittstelle (JSON per POST):
  /belegen     {wer, profil, art, dauer, zweck} -> {zuschlag, grund?, frei_in_s?}
  /verlaengern {wer, dauer}
  /freigeben   {wer}
"""
from __future__ import annotations

import contextlib
import json
import threading
import time
import urllib.request


class Belegungsstelle:
    def __init__(self, url: str, wer: str = "decknamen", dauer: int = 900,
                 takt: float = 300, warte: float = 30, hoechstens_warten: float = 3600):
        self.url = url.rstrip("/")
        self.wer, self.dauer, self.takt = wer, dauer, takt
        self.warte, self.hoechstens_warten = warte, hoechstens_warten
        self._oeffner = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _post(self, pfad: str, **daten) -> dict | None:
        req = urllib.request.Request(f"{self.url}/{pfad}", data=json.dumps(daten).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with self._oeffner.open(req, timeout=5) as r:
                return json.load(r)
        except Exception:
            return None

    @contextlib.contextmanager
    def halten(self, zweck: str, warten=None):
        beginn = time.time()
        while True:
            a = self._post("belegen", wer=self.wer, profil="", art="exklusiv",
                           dauer=self.dauer, zweck=zweck)
            if a is None or a.get("zuschlag") or a.get("grund") == "kein_modell_geladen":
                break
            if time.time() - beginn > self.hoechstens_warten:
                self._post("freigeben", wer=self.wer)
                raise RuntimeError("Das Modell ist seit über einer Stunde anderweitig belegt.")
            if warten:
                frei = a.get("frei_in_s")
                warten("Das Modell wird gerade von anderen gebraucht"
                       + (f", frei in etwa {round(frei / 60)} min" if frei else "") + ".")
            time.sleep(self.warte)

        halt = threading.Event()

        def herzschlag():
            while not halt.wait(self.takt):
                self._post("verlaengern", wer=self.wer, dauer=self.dauer)

        threading.Thread(target=herzschlag, daemon=True).start()
        try:
            yield
        finally:
            halt.set()
            self._post("freigeben", wer=self.wer)
