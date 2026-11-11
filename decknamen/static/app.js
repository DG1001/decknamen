// decknamen: Kleinigkeiten im Browser. Verbindungen nur zur eigenen Seite.
"use strict";

document.addEventListener("DOMContentLoaded", () => {
  // Ablagefläche: gewählte Dateien anzeigen, Ziehen und Fallenlassen.
  const eingabe = document.getElementById("dateien");
  const auswahl = document.getElementById("auswahl");
  if (eingabe && auswahl) {
    const zeigen = () => {
      auswahl.textContent = Array.from(eingabe.files).map((f) => f.name).join(", ");
    };
    eingabe.addEventListener("change", zeigen);
    const flaeche = eingabe.closest(".ablage");
    ["dragenter", "dragover"].forEach((e) => flaeche.addEventListener(e, (ev) => {
      ev.preventDefault();
      flaeche.classList.add("drueber");
    }));
    ["dragleave", "drop"].forEach((e) => flaeche.addEventListener(e, () => flaeche.classList.remove("drueber")));
    flaeche.addEventListener("drop", (ev) => {
      ev.preventDefault();
      if (ev.dataTransfer && ev.dataTransfer.files.length) {
        eingabe.files = ev.dataTransfer.files;
        zeigen();
      }
    });
  }

  // Laufender Auftrag: Stand abfragen, bei Ende neu laden.
  const lauf = document.getElementById("lauf");
  if (lauf && ["wartet", "laeuft"].includes(lauf.dataset.stand)) {
    const balken = document.getElementById("balken");
    const schritt = document.getElementById("schritt");
    const abfragen = async () => {
      try {
        const r = await fetch(lauf.dataset.standUrl, { cache: "no-store" });
        const d = await r.json();
        if (d.stand !== lauf.dataset.stand && !["wartet", "laeuft"].includes(d.stand)) {
          location.reload();
          return;
        }
        if (balken) balken.value = Math.round(d.anteil * 100);
        if (schritt) schritt.textContent = d.schritt;
      } catch (e) { /* nächster Versuch */ }
      setTimeout(abfragen, 1500);
    };
    setTimeout(abfragen, 1500);
  }

  // Ansicht Ergebnis / Original.
  document.querySelectorAll(".umschalter button").forEach((knopf) => {
    knopf.addEventListener("click", () => {
      document.querySelectorAll(".umschalter button").forEach((b) => {
        const an = b === knopf;
        b.setAttribute("aria-pressed", String(an));
        document.getElementById(b.dataset.zeige).hidden = !an;
      });
    });
  });

  // Markierter Text im Blatt wird zum Vorschlag fürs Ergänzen.
  const neu = document.getElementById("neu_text");
  if (neu) {
    document.querySelectorAll(".blatt").forEach((blatt) => {
      blatt.addEventListener("mouseup", () => {
        const s = String(window.getSelection() || "").trim();
        if (s && s.length <= 120 && !s.includes("\n")) {
          neu.value = s;
          neu.classList.add("uebernommen");
        }
      });
    });
  }

  // Ungespeicherte Änderungen: Herunterladen erst nach dem Übernehmen.
  const funde = document.getElementById("funde");
  if (funde) {
    funde.addEventListener("input", () => document.body.classList.add("geaendert"));
  }

  // In die Zwischenablage.
  document.querySelectorAll("[data-kopiere]").forEach((knopf) => {
    knopf.addEventListener("click", async () => {
      const text = document.getElementById(knopf.dataset.kopiere).textContent;
      try {
        await navigator.clipboard.writeText(text);
        knopf.textContent = "Kopiert";
      } catch (e) {
        knopf.textContent = "Kopieren nicht erlaubt, bitte markieren";
      }
    });
  });
});
