"""Prüft, dass das Sprachmodell im eigenen Netz läuft.

Die ganze Anwendung ist nur dann etwas wert, wenn die Dokumente das Haus
nicht verlassen. Deshalb verweigert sie jede LLM-Adresse, die auf eine
öffentliche IP zeigt -- vor dem Start und vor jeder einzelnen Anfrage
(ein Name kann später auf eine andere Adresse zeigen).

Als lokal gilt alles, was nicht global routbar ist: 127.0.0.0/8, ::1,
private Netze (10/8, 172.16/12, 192.168/16, fc00::/7), Link-Local und der
CGNAT-Bereich 100.64.0.0/10, den z. B. Tailscale verwendet.
"""
from __future__ import annotations

import ipaddress
import socket
import urllib.parse


class NichtLokal(Exception):
    pass


def _adressen(host: str) -> list[ipaddress._BaseAddress]:
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        return []
    return sorted({ipaddress.ip_address(i[4][0].split("%")[0]) for i in infos}, key=str)


def _pruefen(url: str) -> tuple[bool, str, list]:
    host = urllib.parse.urlsplit(url).hostname or ""
    ips = _adressen(host) if host else []
    return bool(ips) and all(not ip.is_global for ip in ips), host, ips


def ist_lokal(url: str) -> bool:
    return _pruefen(url)[0]


def pruefe_lokal(url: str, erlaube_entfernt: bool = False) -> None:
    lokal, host, ips = _pruefen(url)
    if lokal or erlaube_entfernt:
        return
    wohin = ", ".join(map(str, ips)) if ips else "nicht auflösbar"
    raise NichtLokal(
        f"Die LLM-Adresse {url} ist nicht lokal ({host} -> {wohin}). "
        "decknamen schickt Dokumente nur an Modelle im eigenen Netz. "
        "Wer das bewusst anders will, startet mit --erlaube-entferntes-llm.")
