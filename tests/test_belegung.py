import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from decknamen.belegung import Belegungsstelle


@pytest.fixture
def stelle():
    aufrufe = []
    antworten = {"belegen": [{"zuschlag": True}]}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            d = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            pfad = self.path.strip("/")
            aufrufe.append((pfad, d))
            liste = antworten.get(pfad) or [{"ok": True}]
            b = json.dumps(liste.pop(0) if len(liste) > 1 else liste[0]).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

    s = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{s.server_port}", aufrufe, antworten
    s.shutdown()


def test_exklusiv_angemeldet_und_danach_freigegeben(stelle):
    url, aufrufe, _ = stelle
    with Belegungsstelle(url, takt=0.05).halten("Pseudonymisierung"):
        pass
    assert aufrufe[0][0] == "belegen"
    assert aufrufe[0][1]["art"] == "exklusiv" and aufrufe[0][1]["wer"] == "decknamen"
    assert aufrufe[-1] == ("freigeben", {"wer": "decknamen"})


def test_herzschlag_verlaengert_waehrend_der_arbeit(stelle):
    import time
    url, aufrufe, _ = stelle
    with Belegungsstelle(url, takt=0.05).halten("x"):
        time.sleep(0.3)
    assert sum(1 for p, _ in aufrufe if p == "verlaengern") >= 3


def test_wartet_bis_zuschlag(stelle):
    url, aufrufe, antworten = stelle
    antworten["belegen"] = [{"zuschlag": False, "grund": "belegt"}, {"zuschlag": True}]
    gemeldet = []
    with Belegungsstelle(url, takt=0.05, warte=0.01).halten("x", warten=gemeldet.append):
        pass
    assert [p for p, _ in aufrufe].count("belegen") == 2
    assert gemeldet


def test_nicht_erreichbar_heisst_weitermachen():
    with Belegungsstelle("http://127.0.0.1:1", takt=0.05).halten("x"):
        pass
