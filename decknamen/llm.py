"""Schlanker Client für OpenAI-kompatible lokale Modelle.

Läuft mit vLLM, llama.cpp (llama-server), Ollama, LM Studio und allem,
was /v1/chat/completions spricht. Bewusst ohne Fremdbibliothek und ohne
Proxy: Umgebungsvariablen wie HTTP_PROXY würden die Dokumente sonst
unbemerkt über einen Dritten leiten.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from .lokal import pruefe_lokal


class LLMFehler(Exception):
    def __init__(self, meldung: str, code: int | None = None):
        super().__init__(meldung)
        self.code = code


class LLM:
    def __init__(self, basis: str, modell: str | None = None, erlaube_entfernt: bool = False,
                 denken_aus: bool = True, zeitlimit: float = 600):
        self.basis = basis.rstrip("/")
        self.erlaube_entfernt = erlaube_entfernt
        pruefe_lokal(self.basis, erlaube_entfernt)
        self._modell = modell
        self.denken_aus = denken_aus
        self.zeitlimit = zeitlimit
        self._schema_geht = True
        self._oeffner = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _anfrage(self, pfad: str, daten: dict | None = None, zeit: float | None = None) -> dict:
        pruefe_lokal(self.basis, self.erlaube_entfernt)
        req = urllib.request.Request(
            self.basis + pfad,
            data=None if daten is None else json.dumps(daten).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with self._oeffner.open(req, timeout=zeit or self.zeitlimit) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            text = e.read().decode(errors="replace")[:300]
            raise LLMFehler(f"Sprachmodell antwortet mit HTTP {e.code}: {text}", e.code) from e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            grund = getattr(e, "reason", e)
            raise LLMFehler(f"Sprachmodell unter {self.basis} nicht erreichbar: {grund}") from e

    def modelle(self) -> list[str]:
        return [m["id"] for m in self._anfrage("/models", zeit=10).get("data", [])]

    def modell_name(self) -> str:
        if not self._modell:
            namen = self.modelle()
            if not namen:
                raise LLMFehler(f"Unter {self.basis} ist kein Modell geladen.")
            self._modell = namen[0]
        return self._modell

    def chat(self, system: str, nutzer: str, schema: dict | None = None,
             max_tokens: int = 4096) -> str:
        leib = {
            "model": self.modell_name(),
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": nutzer}],
            "temperature": 0,
            "max_tokens": max_tokens,
        }
        if self.denken_aus:
            leib["chat_template_kwargs"] = {"enable_thinking": False}
        if schema and self._schema_geht:
            leib["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "funde", "schema": schema}}
        try:
            d = self._anfrage("/chat/completions", leib)
        except LLMFehler as e:
            if e.code == 400 and "response_format" in leib:
                self._schema_geht = False            # Server kennt kein Schema
                leib.pop("response_format")
            elif not (e.code and e.code >= 500):
                raise
            d = self._anfrage("/chat/completions", leib)   # einmal noch
        try:
            inhalt = d["choices"][0]["message"].get("content") or ""
        except (KeyError, IndexError, TypeError) as e:
            raise LLMFehler(f"Unerwartete Antwort des Sprachmodells: {str(d)[:200]}") from e
        inhalt = re.sub(r"<think>.*?</think>", "", inhalt, flags=re.S)
        if "</think>" in inhalt:
            inhalt = inhalt.split("</think>", 1)[1]
        return inhalt.strip()
