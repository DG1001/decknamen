import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from decknamen.llm import LLM, LLMFehler
from decknamen.lokal import NichtLokal


class _Stelle:
    """Ein winziger OpenAI-kompatibler Server für die Tests."""

    def __init__(self, antworten):
        self.antworten = list(antworten)
        self.anfragen = []
        stelle = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _schicken(self, code, d):
                b = json.dumps(d).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_GET(self):
                self._schicken(200, {"data": [{"id": "testmodell"}]})

            def do_POST(self):
                d = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                stelle.anfragen.append(d)
                code, inhalt = stelle.antworten.pop(0)
                if code != 200:
                    return self._schicken(code, {"error": inhalt})
                self._schicken(200, {"choices": [{"message": {"content": inhalt}}]})

        self.server = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.basis = f"http://127.0.0.1:{self.server.server_port}/v1"

    def schliessen(self):
        self.server.shutdown()


@pytest.fixture
def stelle():
    s = []

    def bauen(*antworten):
        s.append(_Stelle(antworten))
        return s[-1]
    yield bauen
    for x in s:
        x.schliessen()


def test_modell_wird_vom_server_erfragt(stelle):
    st = stelle()
    assert LLM(st.basis).modell_name() == "testmodell"


def test_anfrage_mit_schema_ohne_denken_bei_temperatur_null(stelle):
    st = stelle((200, '{"funde": []}'))
    antwort = LLM(st.basis).chat("sys", "nutzer", schema={"type": "object"})
    assert antwort == '{"funde": []}'
    a = st.anfragen[0]
    assert a["model"] == "testmodell" and a["temperature"] == 0
    assert a["response_format"]["type"] == "json_schema"
    assert a["chat_template_kwargs"] == {"enable_thinking": False}
    assert [m["role"] for m in a["messages"]] == ["system", "user"]


def test_ohne_schema_wenn_der_server_es_ablehnt(stelle):
    st = stelle((400, "response_format not supported"), (200, '{"funde": []}'))
    assert LLM(st.basis).chat("s", "n", schema={"type": "object"}) == '{"funde": []}'
    assert "response_format" not in st.anfragen[1]


def test_denkspuren_werden_entfernt(stelle):
    st = stelle((200, "<think>hm, Anna?</think>\n{\"funde\": []}"), (200, "nur gedacht</think>{}"))
    llm = LLM(st.basis)
    assert llm.chat("s", "n") == '{"funde": []}'
    assert llm.chat("s", "n") == "{}"


def test_fehler_des_servers_wird_gemeldet(stelle):
    st = stelle((500, "kaputt"), (500, "kaputt"))
    with pytest.raises(LLMFehler, match="500"):
        LLM(st.basis).chat("s", "n")


def test_entfernte_adresse_wird_nie_angefragt():
    with pytest.raises(NichtLokal):
        LLM("http://8.8.8.8/v1", modell="x").chat("s", "n")
