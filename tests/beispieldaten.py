#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Erzeugt eine SYNTHETISCHE Ist-Daten-Datei im Originalformat von opentransportdata.swiss.
Nur zum Testen der Analyse und für die Dashboard-Vorschau – alle Werte sind zufällig.

Neben der CSV entsteht <datei>.erwartet.json mit den Fahrten, die die Analyse
finden MUSS (Schweizer Halte, gemessene Zeiten, ≥ 60 Min., plausibel).

  python tests/beispieldaten.py 2026-11-14 /tmp/2026-11-14_istdaten.csv
"""
from __future__ import annotations

import datetime as dt
import json
import math
import random
import sys
import zlib
from pathlib import Path

KOPF = ["BETRIEBSTAG", "FAHRT_BEZEICHNER", "BETREIBER_ID", "BETREIBER_ABK", "BETREIBER_NAME",
        "PRODUKT_ID", "LINIEN_ID", "LINIEN_TEXT", "UMLAUF_ID", "VERKEHRSMITTEL_TEXT",
        "ZUSATZFAHRT_TF", "FAELLT_AUS_TF", "BPUIC", "HALTESTELLEN_NAME", "ANKUNFTSZEIT",
        "AN_PROGNOSE", "AN_PROGNOSE_STATUS", "ABFAHRTSZEIT", "AB_PROGNOSE", "AB_PROGNOSE_STATUS",
        "DURCHFAHRT_TF", "SLOID"]

BETREIBER = {
    "SBB": ("85:11", "Schweizerische Bundesbahnen SBB"),
    "BLS": ("85:33", "BLS AG (bls)"),
    "SOB": ("85:82", "Schweizerische Südostbahn (sob)"),
    "THURBO": ("85:65", "THURBO"),
    "VBL": ("85:823", "Verkehrsbetriebe Luzern"),
    "VBZ": ("85:3849", "Verkehrsbetriebe Zürich"),
    "PAG": ("85:801", "PostAuto AG"),
}

AUSLAND = {"Frankfurt (Main) Hbf": "8000105", "Mannheim Hbf": "8000244", "Karlsruhe Hbf": "8000191",
           "Como S. Giovanni": "8301700", "Milano Centrale": "8300046"}

LINIEN = [
    dict(b="SBB", p="Zug", vm="IC", l="IC1", nr=700, takt=60, von=6, bis=22, halte=[
        ("Genève-Aéroport", 0), ("Genève", 6), ("Nyon", 19), ("Lausanne", 42), ("Fribourg/Freiburg", 80),
        ("Bern", 102), ("Zürich HB", 158), ("Zürich Flughafen", 170), ("Winterthur", 185), ("St. Gallen", 222)]),
    dict(b="SBB", p="Zug", vm="IC", l="IC5", nr=500, takt=60, von=6, bis=21, halte=[
        ("Lausanne", 0), ("Yverdon-les-Bains", 22), ("Neuchâtel", 42), ("Biel/Bienne", 60),
        ("Solothurn", 76), ("Olten", 98), ("Aarau", 108), ("Zürich HB", 134)]),
    dict(b="SBB", p="Zug", vm="IC", l="IC2", nr=860, takt=60, von=6, bis=21, halte=[
        ("Zürich HB", 0), ("Zug", 22), ("Arth-Goldau", 37), ("Bellinzona", 92), ("Lugano", 112)]),
    dict(b="SBB", p="Zug", vm="IC", l="IC6", nr=1060, takt=60, von=6, bis=21, halte=[
        ("Basel SBB", 0), ("Olten", 25), ("Bern", 56), ("Thun", 76), ("Spiez", 87), ("Visp", 115), ("Brig", 125)]),
    dict(b="SBB", p="Zug", vm="IR", l="IR26", nr=2300, takt=60, von=6, bis=22, halte=[
        ("Basel SBB", 0), ("Liestal", 10), ("Olten", 26), ("Zofingen", 35), ("Sursee", 47), ("Luzern", 64)]),
    dict(b="SBB", p="Zug", vm="EC", l="EC", nr=150, takt=240, von=7, bis=19, halte=[
        ("Frankfurt (Main) Hbf", 0), ("Mannheim Hbf", 38), ("Karlsruhe Hbf", 63), ("Basel Bad Bf", 135),
        ("Basel SBB", 142), ("Olten", 168), ("Luzern", 205), ("Arth-Goldau", 232), ("Bellinzona", 287),
        ("Lugano", 307), ("Chiasso", 327), ("Como S. Giovanni", 333), ("Milano Centrale", 378)]),
    dict(b="BLS", p="Zug", vm="RE", l="RE", nr=4000, takt=120, von=6, bis=20, halte=[
        ("Bern", 0), ("Thun", 20), ("Spiez", 31), ("Frutigen", 45), ("Kandersteg", 60), ("Goppenstein", 72),
        ("Brig", 92)]),
    dict(b="SOB", p="Zug", vm="IR", l="IR46", nr=2400, takt=60, von=6, bis=21, halte=[
        ("St. Gallen", 0), ("Herisau", 10), ("Wattwil", 30), ("Rapperswil", 55), ("Pfäffikon SZ", 63),
        ("Biberbrugg", 78), ("Arth-Goldau", 98), ("Küssnacht am Rigi", 108), ("Luzern", 125)]),
    dict(b="SBB", p="Zug", vm="S", l="S3", nr=18000, takt=30, von=5, bis=23, halte=[
        ("Wetzikon", 0), ("Uster", 7), ("Dübendorf", 15), ("Zürich Stadelhofen", 23), ("Zürich HB", 27),
        ("Zürich Hardbrücke", 30), ("Zürich Altstetten", 34), ("Dietikon", 42), ("Baden", 54),
        ("Brugg AG", 62), ("Aarau", 76)]),
    dict(b="SBB", p="Zug", vm="S", l="S1", nr=21100, takt=30, von=5, bis=23, halte=[
        ("Sursee", 0), ("Sempach-Neuenkirch", 6), ("Emmenbrücke", 14), ("Luzern", 20), ("Ebikon", 27),
        ("Rotkreuz", 36), ("Cham", 41), ("Zug", 46), ("Baar", 50)]),
    dict(b="THURBO", p="Zug", vm="S", l="S8", nr=20800, takt=30, von=5, bis=23, halte=[
        ("Winterthur", 0), ("Frauenfeld", 15), ("Weinfelden", 28), ("Kreuzlingen", 46), ("Romanshorn", 60)]),
    dict(b="VBL", p="Bus", vm="B", l="1", nr=1, takt=10, von=5, bis=24, halte=[
        ("Kriens, Obernau Dorf", 0), ("Kriens, Zentrum Pilatus", 5), ("Kriens, Busschleife", 8),
        ("Luzern, Eichhof", 12), ("Luzern, Pilatusplatz", 16), ("Luzern, Bahnhof", 20),
        ("Luzern, Schwanenplatz", 23), ("Luzern, Löwenplatz", 26), ("Luzern, Maihof", 30),
        ("Ebikon, Fildern", 36), ("Ebikon, Bahnhof", 40)]),
    dict(b="VBZ", p="Tram", vm="T", l="4", nr=4, takt=8, von=5, bis=24, halte=[
        ("Zürich, Tiefenbrunnen", 0), ("Zürich, Kreuzstrasse", 3), ("Zürich, Bellevue", 7),
        ("Zürich, Central", 11), ("Zürich, Bahnhofquai/HB", 13), ("Zürich, Limmatplatz", 17),
        ("Zürich, Escher-Wyss-Platz", 21), ("Zürich, Hardturm", 25), ("Zürich, Werdhölzli", 29)]),
    dict(b="PAG", p="Bus", vm="B", l="182", nr=182, takt=60, von=6, bis=21, halte=[
        ("Chur, Bahnhof", 0), ("Malix, Post", 14), ("Churwalden, Post", 22), ("Parpan, Post", 27),
        ("Lenzerheide/Lai, Post", 36), ("Lantsch/Lenz, Post", 44), ("Tiefencastel, Bahnhof", 55)]),
]

GEMESSEN = ("REAL", "GESCHAETZT")


def code(name: str) -> str:
    if name in AUSLAND:
        return AUSLAND[name]
    return "85" + f"{zlib.crc32(name.encode()) % 100000:05d}"


def zeit(t: dt.datetime, sekunden: bool) -> str:
    return t.strftime("%d.%m.%Y %H:%M:%S" if sekunden else "%d.%m.%Y %H:%M")


def erzeuge(datum: dt.date, ziel: Path, faktor: float | None = None) -> dict:
    rnd = random.Random(int(datum.strftime("%Y%m%d")))
    # Tagescharakter: ruhig, normal oder chaotisch
    zufall = rnd.choices([0.4, 1.0, 2.6], weights=[2, 6, 2])[0]
    faktor = zufall if faktor is None else faktor
    stoerungen = []
    for _ in range(rnd.choice([0, 1, 1, 2]) if faktor >= 1 else 0):
        linie = rnd.choice([l for l in LINIEN if l["p"] == "Zug" and l["vm"] != "S"] * 3 + [l for l in LINIEN if l["vm"] == "S"])
        start = rnd.randint(6, 18)
        stoerungen.append(dict(l=linie["l"], ab_idx=rnd.randrange(1, len(linie["halte"]) - 1),
                               von=start, bis=start + rnd.randint(1, 3), plus=rnd.uniform(55, 165)))

    zeilen, erwartet = [], {}
    tag_str = datum.strftime("%d.%m.%Y")
    basis = dt.datetime.combine(datum, dt.time())
    spezial = {"durchfahrt": False, "unplausibel": False, "prognose": False}

    for linie in LINIEN:
        op_id, op_name = BETREIBER[linie["b"]]
        for richtung in (0, 1):
            halte = linie["halte"] if richtung == 0 else \
                [(n, linie["halte"][-1][1] - m) for n, m in reversed(linie["halte"])]
            abfahrt = basis + dt.timedelta(hours=linie["von"], minutes=rnd.randint(0, 9))
            k = 0
            while abfahrt.hour < linie["bis"] and abfahrt.date() == datum:
                k += 1
                nr = linie["nr"] + 2 * k + richtung
                fahrt = f"{op_id}:{nr}:00{richtung + 1}" if linie["p"] == "Zug" else \
                    f"{op_id}:{linie['l']}-{richtung}{k:04d}:{datum.day:02d}"
                n = len(halte)

                # Verspätungsverlauf in Sekunden
                vsp = [max(0.0, rnd.gauss(50, 70)) for _ in range(n)]
                p_inz = {"Zug": 0.16, "Bus": 0.02, "Tram": 0.015}[linie["p"]] * faktor
                if linie["vm"] == "S":
                    p_inz *= 0.3
                elif linie["vm"] == "EC":
                    p_inz = min(0.9, p_inz * 3)
                if rnd.random() < p_inz:
                    s = rnd.randrange(0, n)
                    d = min(330.0, rnd.lognormvariate(math.log(32 if linie["p"] == "Zug" else 12), 0.8)) * 60
                    rampe = rnd.choice([1, 1, 2, 3])
                    for i in range(s, n):
                        vsp[i] += d * min(1.0, (i - s + 1) / rampe) * (0.96 ** (i - s))
                for st in stoerungen:
                    if st["l"] == linie["l"] and st["von"] <= abfahrt.hour < st["bis"]:
                        idx = st["ab_idx"] if richtung == 0 else n - 1 - st["ab_idx"]
                        for i in range(idx, n):
                            vsp[i] += st["plus"] * 60 * (0.98 ** (i - idx))

                komplett = rnd.random() < 0.004 * faktor
                teil_ab = n - rnd.randint(1, 3) if (not komplett and rnd.random() < 0.004 * faktor) else n
                zusatz = linie["p"] == "Zug" and faktor > 2 and rnd.random() < 0.01

                # Testfälle, die NICHT in die Liste dürfen
                nur_prognose = linie["p"] == "Bus" and not spezial["prognose"] and k == 20
                if nur_prognose:
                    spezial["prognose"] = True
                    vsp = [95 * 60 + i * 30 for i in range(n)]
                unplausibel_idx = None
                if linie["l"] == "IC6" and not spezial["unplausibel"] and k == 5:
                    spezial["unplausibel"] = True
                    vsp = [30.0] * n
                    unplausibel_idx = n // 2
                    komplett, teil_ab = False, n

                beste = None
                for i, (name, m) in enumerate(halte):
                    soll_an = abfahrt + dt.timedelta(minutes=m)
                    verweil = 1 if (linie["p"] == "Zug" and 0 < i < n - 1) else 0
                    soll_ab = soll_an + dt.timedelta(minutes=verweil)
                    ausfall = komplett or i >= teil_ab
                    ausland = name in AUSLAND
                    if nur_prognose:
                        status = "PROGNOSE"
                    elif i == unplausibel_idx:
                        status = "REAL"
                    elif linie["p"] == "Zug":
                        status = rnd.choices(["REAL", "GESCHAETZT", "PROGNOSE"], [92, 4, 4])[0]
                    else:
                        status = rnd.choices(["REAL", "GESCHAETZT", "PROGNOSE", "UNBEKANNT"], [35, 25, 35, 5])[0]
                    v_an = vsp[i]
                    v_ab = max(0.0, vsp[i] - 30 * verweil)
                    if i == unplausibel_idx:
                        v_an = v_ab = 1440 * 60.0
                    v_an, v_ab = round(v_an), round(v_ab)  # die Datei kennt nur ganze Sekunden
                    ist_an = soll_an + dt.timedelta(seconds=v_an)
                    ist_ab = soll_ab + dt.timedelta(seconds=v_ab)
                    erster, letzter = i == 0, i == n - 1
                    leer = status == "UNBEKANNT" or ausfall
                    zeilen.append([
                        tag_str, fahrt, op_id, linie["b"], op_name, linie["p"],
                        str(nr) if linie["p"] == "Zug" else linie["l"], linie["l"], "", linie["vm"],
                        "true" if zusatz else "false", "true" if ausfall else "false",
                        code(name), name,
                        "" if erster else zeit(soll_an, False),
                        "" if (erster or leer) else zeit(ist_an, True),
                        "" if erster else (status if not ausfall else "PROGNOSE"),
                        "" if letzter else zeit(soll_ab, False),
                        "" if (letzter or leer) else zeit(ist_ab, True),
                        "" if letzter else (status if not ausfall else "PROGNOSE"),
                        "false", "" if ausland else f"ch:1:sloid:{code(name)[2:]}",
                    ])
                    # Erwartung: nur Schweizer Halte, gemessene Zeiten, plausibel
                    if not ausland and not ausfall and status in GEMESSEN:
                        werte = ([] if erster else [v_an]) + ([] if letzter else [v_ab])
                        for w in werte:
                            if abs(w) <= 720 * 60 and (beste is None or w > beste[0]):
                                beste = (w, name)

                    # eine Durchfahrt mit riesigem Wert – darf nicht zählen
                    if linie["l"] == "IC1" and richtung == 0 and i == 5 and k == 3 and not spezial["durchfahrt"]:
                        spezial["durchfahrt"] = True
                        t = soll_ab + dt.timedelta(minutes=20)
                        zeilen.append([tag_str, fahrt, op_id, linie["b"], op_name, "Zug", str(nr), linie["l"], "",
                                       linie["vm"], "false", "false", "8500999", "Rothrist",
                                       zeit(t, False), zeit(t + dt.timedelta(minutes=200), True), "REAL",
                                       zeit(t, False), zeit(t + dt.timedelta(minutes=200), True), "REAL",
                                       "true", "ch:1:sloid:999"])
                if beste and beste[0] >= 3600:
                    erwartet[fahrt] = {"max": int(beste[0] / 60), "wo": beste[1]}
                abfahrt += dt.timedelta(minutes=linie["takt"])

    rnd.shuffle(zeilen)  # echte Dateien sind auch nicht sauber sortiert
    ziel.parent.mkdir(parents=True, exist_ok=True)
    with open(ziel, "w", encoding="utf-8", newline="") as f:
        f.write(";".join(KOPF) + "\n")
        for z in zeilen:
            f.write(";".join(z) + "\n")
    info = {"datum": datum.isoformat(), "zeilen": len(zeilen), "faktor": faktor,
            "stoerungen": stoerungen, "erwartet": erwartet}
    Path(str(ziel) + ".erwartet.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    return info


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    i = erzeuge(dt.date.fromisoformat(sys.argv[1]), Path(sys.argv[2]))
    print(f"{i['datum']}: {i['zeilen']} Zeilen, {len(i['erwartet'])} Fahrten ≥ 60 Min. erwartet")
