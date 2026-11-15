#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Prüft die Analyse gegen synthetische Ist-Daten mit bekanntem Ergebnis.

  python tests/test_analyse.py
"""
from __future__ import annotations

import datetime as dt
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
sys.path.insert(0, str(ROOT / "tests"))

import analyse  # noqa: E402
import beispieldaten  # noqa: E402

QUELLE = {"datensatz": "test", "datei": "test.csv", "url": None}


def pruefe_tag(datum: str) -> tuple[dict, dict]:
    d = dt.date.fromisoformat(datum)
    with tempfile.TemporaryDirectory() as tmp:
        csv = Path(tmp) / f"{datum}_istdaten.csv"
        info = beispieldaten.erzeuge(d, csv)
        tag = analyse.analysiere(csv, d, QUELLE)
    erwartet, gefunden = info["erwartet"], {t["id"]: t for t in tag["liste"]}

    assert set(gefunden) == set(erwartet), f"{datum}: Unterschied {set(gefunden) ^ set(erwartet)}"
    for fid, e in erwartet.items():
        assert gefunden[fid]["max"] == e["max"], f"{fid}: {gefunden[fid]['max']} statt {e['max']}"
        assert gefunden[fid]["wo"] == e["wo"], f"{fid}: {gefunden[fid]['wo']} statt {e['wo']}"
    k = tag["kennzahlen"]
    assert k["ab60"] == len(erwartet)
    assert k["unplausibel"] >= 1, "Datenfehler (+24 Std.) wurde nicht ausgefiltert"
    werte = [t["max"] for t in tag["liste"]]
    assert werte == sorted(werte, reverse=True), "Liste nicht absteigend sortiert"
    for t in tag["liste"]:
        assert all(h[0] not in beispieldaten.AUSLAND for h in t["halte"]), "Auslandhalt im Verlauf"
        assert all(h[0] != "Rothrist" for h in t["halte"]), "Durchfahrt im Verlauf"
        assert t["max"] == max(h[2] for h in t["halte"] if h[2] is not None)
        if t["linie"] == "EC":
            assert t["int"] and {t["von"], t["nach"]} & set(beispieldaten.AUSLAND)
    assert tag["basis"] == "gemessen"
    assert sum(tag["stunden"]) == k["ab60"]
    assert tag["text"].startswith(tag["titel"])
    return tag, info


def seite(*links: str) -> bytes:
    """Nachbau der Datensatz-Seite mit Download-Links."""
    return ("<html><body>" + "".join(f'<a href="{l}">x</a>' for l in links) + "</body></html>").encode()


def pruefe_katalog() -> None:
    html_seite = seite(
        "/de/dataset/ist-daten-v2/resource/1",
        "https://x/dataset/a/resource/3/download/2026-11-14_istdaten.csv",  # neuste Version zuerst
        "https://x/dataset/a/resource/2/download/2026-11-14_istdaten.csv",
        "https://x/dataset/a/resource/1/download/2026-11-13_istdaten.csv",
    )
    alt = analyse.http_get
    analyse.http_get = lambda url, timeout=60: io.BytesIO(html_seite)
    analyse._katalog.clear()
    try:
        r = analyse.finde_datei(dt.date(2026, 11, 14))
        assert r and r["url"] == "https://x/dataset/a/resource/3/download/2026-11-14_istdaten.csv", r
        assert r["datensatz"] == "ist-daten-v2" and r["datei"] == "2026-11-14_istdaten.csv"
        assert analyse.finde_datei(dt.date(2026, 11, 15)) is None
    finally:
        analyse.http_get = alt
        analyse._katalog.clear()


def pruefe_ablauf() -> None:
    """Ganzer Lauf wie in GitHub Actions: Katalog → Download → Analyse → Index, zweimal."""
    heute = dt.date(2026, 11, 15)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        tage = [heute - dt.timedelta(days=i) for i in (3, 2, 1)]
        for d in tage[:2]:  # der letzte Tag ist «noch nicht veröffentlicht»
            beispieldaten.erzeuge(d, tmp / "quelle" / f"{d}_istdaten.csv")
        html_seite = seite(*[f"https://x/download/{d}_istdaten.csv" for d in tage[:2]])
        alt = (analyse.http_get, analyse.herunterladen, analyse.heute_ch)
        analyse.http_get = lambda url, timeout=60: io.BytesIO(html_seite)
        analyse.herunterladen = lambda url, ziel: shutil.copy(tmp / "quelle" / Path(url).name, ziel)
        analyse.heute_ch = lambda: heute
        analyse._katalog.clear()
        try:
            ausgabe = tmp / "data"
            assert analyse.main(["--ausgabe", str(ausgabe), "--tage", "3"]) == 0
            idx = json.loads((ausgabe / "index.json").read_text())
            assert [t["datum"] for t in idx["tage"]] == [str(d) for d in tage[:2]]
            zweiter = json.loads((ausgabe / "tage" / f"{tage[1]}.json").read_text())
            assert "Vortag" in zweiter["text"] or zweiter["kennzahlen"]["ab60"] == 0
            vorher = (ausgabe / "tage" / f"{tage[0]}.json").stat().st_mtime
            assert analyse.main(["--ausgabe", str(ausgabe), "--tage", "3"]) == 0  # nichts Neues
            assert (ausgabe / "tage" / f"{tage[0]}.json").stat().st_mtime == vorher
            assert idx["linien_30"] or all(t["ab60"] == 0 for t in idx["tage"])
        finally:
            analyse.http_get, analyse.herunterladen, analyse.heute_ch = alt
            analyse._katalog.clear()


if __name__ == "__main__":
    pruefe_katalog()
    print("✓ Katalog-Suche")
    pruefe_ablauf()
    print("✓ Ganzer Ablauf (Nachholen, Index, kein Doppellauf)")
    n = 0
    for i in range(12):
        datum = (dt.date(2026, 10, 1) + dt.timedelta(days=i)).isoformat()
        tag, info = pruefe_tag(datum)
        n += len(info["erwartet"])
        print(f"✓ {datum}: {tag['kennzahlen']['ab60']:3d} Fahrten ≥ 60 Min. korrekt gefunden "
              f"(max. {tag['kennzahlen']['max']} Min., {info['zeilen']} Zeilen, {tag['dauer_s']} s)")
    print(f"\nAlle Tests bestanden – {n} erwartete Fahrten exakt gefunden.")
