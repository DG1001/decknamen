import socket

import pytest

from decknamen.lokal import ist_lokal, pruefe_lokal, NichtLokal


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8889/v1",
    "http://localhost:11434/v1",
    "http://[::1]:8000/v1",
    "http://192.168.1.20:8000/v1",
    "http://10.0.0.5/v1",
    "http://100.64.0.15:8889/v1",      # Tailscale (CGNAT-Bereich)
])
def test_lokale_adressen(url):
    assert ist_lokal(url)


@pytest.mark.parametrize("url", [
    "https://api.openai.com/v1",
    "http://8.8.8.8/v1",
    "http://[2001:4860:4860::8888]/v1",
])
def test_entfernte_adressen(url, monkeypatch):
    echt = socket.getaddrinfo

    def falsch_aufloesen(host, *a, **k):
        if host == "api.openai.com":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("104.18.7.192", 443))]
        return echt(host, *a, **k)

    monkeypatch.setattr(socket, "getaddrinfo", falsch_aufloesen)
    assert not ist_lokal(url)


def test_name_der_auf_lokal_und_entfernt_zeigt_gilt_als_entfernt(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 80))])
    assert not ist_lokal("http://gemischt.example/v1")


def test_unaufloesbarer_name_gilt_als_entfernt(monkeypatch):
    def nie(*a, **k):
        raise socket.gaierror("nicht gefunden")
    monkeypatch.setattr(socket, "getaddrinfo", nie)
    assert not ist_lokal("http://gibtsnicht.invalid/v1")


def test_pruefe_lokal_wirft_mit_erklaerung():
    with pytest.raises(NichtLokal, match="8.8.8.8"):
        pruefe_lokal("http://8.8.8.8/v1")


def test_pruefe_lokal_laesst_entfernt_zu_wenn_ausdruecklich_erlaubt():
    pruefe_lokal("http://8.8.8.8/v1", erlaube_entfernt=True)
