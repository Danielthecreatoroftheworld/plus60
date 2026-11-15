#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Plus 60 – die grössten Verspätungen im Schweizer ÖV, jeden Tag neu.

Holt die Ist-Daten des Vortags von opentransportdata.swiss, sucht alle Fahrten,
die an einem Schweizer Halt mindestens 60 Minuten verspätet waren, und schreibt
die Tagesanalyse als JSON nach docs/data/ (dort liest sie das Dashboard).

Datenquelle: opentransportdata.swiss, «Ist-Daten v2» (Open Data, Quellenangabe Pflicht)

Beispiele
  python pipeline/analyse.py                      # fehlende Tage nachholen (Standard)
  python pipeline/analyse.py --tage 30            # bis 30 Tage rückwirkend
  python pipeline/analyse.py --datum 2026-11-14   # genau diesen Tag (neu) rechnen
  python pipeline/analyse.py --csv datei.csv --datum 2026-11-14   # lokale Datei
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb

# ---------------------------------------------------------------- Einstellungen

ROOT = Path(__file__).resolve().parent.parent
STANDARD_AUSGABE = ROOT / "docs" / "data"

CKAN = "https://data.opentransportdata.swiss"
DATENSAETZE = ["ist-daten-v2", "istdaten"]  # v2 zuerst (vollständiger), v1 als Reserve
USER_AGENT = "plus60-verspaetungsanalyse/1.0 (open data research; github actions)"

SCHWELLE = 60                 # Minuten – ab hier kommt eine Fahrt in die Liste
STUFEN = (60, 90, 120, 180)   # für die Kennzahlen
MAX_PLAUSIBEL = 12 * 60       # mehr als 12 Std. Abweichung = Datenfehler
MAX_LISTE = 300               # so viele Fahrten pro Tag kommen mit Verlauf ins JSON
ERSTLAUF_TAGE = 14            # beim allerersten Lauf so weit zurück
STANDARD_TAGE = 7             # danach: Lücken der letzten 7 Tage nachholen

# Status der Ist-Zeiten (Datei liefert deutsche Werte, Doku nennt englische)
GEMESSEN = ("REAL", "GESCHAETZT", "ESTIMATED")
PROGNOSE = ("PROGNOSE", "FORECAST", "")

TZ = ZoneInfo("Europe/Zurich")
ZEITFORMATE = ("['%d.%m.%Y %H:%M:%S', '%d.%m.%Y %H:%M', '%Y-%m-%d %H:%M:%S', "
               "'%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M']")

PFLICHT = ["FAHRT_BEZEICHNER", "HALTESTELLEN_NAME", "ANKUNFTSZEIT", "AN_PROGNOSE",
           "AN_PROGNOSE_STATUS", "ABFAHRTSZEIT", "AB_PROGNOSE", "AB_PROGNOSE_STATUS"]

WOCHENTAGE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
MONATE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
          "September", "Oktober", "November", "Dezember"]


def log(text: str) -> None:
    print(text, flush=True)


# ---------------------------------------------------------------- Formatierung

def zahl(n: int) -> str:
    """Schweizer Tausendertrennung: 12’345"""
    return f"{int(n):,}".replace(",", "’")


def dauer(minuten: int) -> str:
    h, m = divmod(int(minuten), 60)
    if h == 0:
        return f"{m} Min."
    return f"{h} Std." if m == 0 else f"{h} Std. {m} Min."


def datum_lang(d: dt.date) -> str:
    return f"{WOCHENTAGE[d.weekday()]}, {d.day}. {MONATE[d.month - 1]} {d.year}"


def hhmm(ts) -> str | None:
    return ts.strftime("%H:%M") if ts else None


def minuten(x) -> int | None:
    """Verspätung in ganzen Minuten (angefangene Minute zählt nicht, wie bei der SBB)."""
    return None if x is None else int(x)


def jetzt_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def produkt_label(p: str | None) -> str:
    if not p or not p.strip():
        return "Unbekannt"
    p = p.strip()
    return p[:1].upper() + p[1:].lower()


def linie_anzeige(linie: str | None, vm: str | None, produkt: str) -> str:
    l, v = (linie or "").strip(), (vm or "").strip()
    if produkt == "Zug":
        if not l:
            return v or "Zug"
        if l.isdigit():
            return f"{v} {l}".strip()
        m = re.fullmatch(r"([A-Z]{2,4})(\d+)", l)
        if m and not m.group(1).startswith("S"):  # «IC1» → «IC 1», S-Bahn bleibt «S3»
            return f"{m.group(1)} {m.group(2)}"
        return l
    if not l:
        return f"{produkt} {v}".strip() if produkt != "Unbekannt" else (v or "?")
    if produkt != "Unbekannt" and produkt.lower() not in l.lower():
        return f"{produkt} {l}"
    return l


def zugnummer(linien_id: str | None, fahrt: str, produkt: str) -> str | None:
    if produkt != "Zug":
        return None
    for kandidat in ((linien_id or "").strip(), (fahrt.split(":") + ["", "", ""])[2]):
        if kandidat.isdigit():
            return str(int(kandidat))
    return None


# ---------------------------------------------------------------- Download

_katalog: dict[str, list | None] = {}


def http_get(url: str, timeout: int = 60):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    return urllib.request.urlopen(req, timeout=timeout)


def ckan_ressourcen(datensatz: str) -> list | None:
    """Alle Dateien eines Datensatzes laut CKAN-Katalog (None = nicht erreichbar)."""
    if datensatz in _katalog:
        return _katalog[datensatz]
    url = f"{CKAN}/api/3/action/package_show?id={urllib.parse.quote(datensatz)}"
    letzter_fehler = None
    for versuch in range(3):
        try:
            with http_get(url) as r:
                antwort = json.load(r)
            if not antwort.get("success"):
                raise RuntimeError(antwort.get("error"))
            _katalog[datensatz] = antwort["result"].get("resources", [])
            return _katalog[datensatz]
        except Exception as e:  # noqa: BLE001 – wir wollen jeden Fehler protokollieren
            letzter_fehler = e
            time.sleep(5 * (versuch + 1))
    log(f"  ! Katalog «{datensatz}» nicht erreichbar: {letzter_fehler}")
    _katalog[datensatz] = None
    return None


def finde_datei(datum: dt.date) -> dict | None:
    """Sucht die Ist-Daten-Datei eines Tages. None = (noch) nicht veröffentlicht."""
    tag = datum.isoformat()
    erreichbar = False
    for ds in DATENSAETZE:
        ressourcen = ckan_ressourcen(ds)
        if ressourcen is None:
            continue
        erreichbar = True
        treffer = []
        for r in ressourcen:
            url = r.get("url") or ""
            name = r.get("name") or r.get("identifier") or Path(urllib.parse.urlparse(url).path).name
            text = f"{name} {url}".lower()
            if tag in text and re.search(r"ist[-_]?daten", text) and url:
                treffer.append((r.get("last_modified") or r.get("created") or "", name, url))
        if treffer:
            treffer.sort(reverse=True)  # neuste Version zuerst, falls doppelt
            _, name, url = treffer[0]
            return {"datensatz": ds, "datei": name, "url": url}
    if not erreichbar:
        raise RuntimeError("Der Datenkatalog von opentransportdata.swiss ist nicht erreichbar.")
    return None


def herunterladen(url: str, ziel: Path) -> None:
    for versuch in range(1, 4):
        try:
            t0 = time.time()
            with http_get(url, timeout=180) as r, open(ziel, "wb") as f:
                shutil.copyfileobj(r, f, length=1 << 20)
            mb = ziel.stat().st_size / 1e6
            if mb < 1:
                raise RuntimeError(f"Datei verdächtig klein ({mb:.2f} MB)")
            log(f"  ↓ {mb:.0f} MB in {time.time() - t0:.0f} s")
            return
        except Exception as e:  # noqa: BLE001
            log(f"  ! Download-Versuch {versuch} fehlgeschlagen: {e}")
            if versuch == 3:
                raise
            time.sleep(20 * versuch)


def entpacken(pfad: Path) -> Path:
    """Falls die Datei gezippt oder gzip-komprimiert kommt: lesbar machen."""
    with open(pfad, "rb") as f:
        magie = f.read(4)
    if magie.startswith(b"PK"):
        with zipfile.ZipFile(pfad) as z:
            csvs = [n for n in z.namelist() if n.lower().endswith(".csv")]
            if not csvs:
                raise RuntimeError("ZIP enthält keine CSV-Datei")
            ziel = pfad.with_suffix(".entpackt.csv")
            with z.open(csvs[0]) as quelle, open(ziel, "wb") as out:
                shutil.copyfileobj(quelle, out, length=1 << 20)
        return ziel
    if magie.startswith(b"\x1f\x8b") and pfad.suffix != ".gz":
        neu = pfad.with_name(pfad.name + ".gz")
        pfad.rename(neu)
        return neu
    return pfad


def trennzeichen(pfad: Path) -> str:
    oeffnen = gzip.open if pfad.suffix == ".gz" else open
    with oeffnen(pfad, "rb") as f:
        zeile = f.readline().decode("utf-8-sig", errors="replace")
    return ";" if zeile.count(";") >= zeile.count(",") else ","


# ---------------------------------------------------------------- Analyse

def analysiere(pfad: Path, datum: dt.date, quelle: dict, vortag: dict | None = None,
               mit_prognose: bool = False) -> dict:
    t0 = time.time()
    con = duckdb.connect()
    con.execute("SET enable_progress_bar = false")
    sql_pfad = str(pfad).replace("'", "''")
    quelle_sql = (f"read_csv('{sql_pfad}', delim='{trennzeichen(pfad)}', header=true, all_varchar=true, "
                  f"quote='\"', escape='\"', ignore_errors=true, null_padding=true)")

    spalten = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {quelle_sql}").fetchall()]
    norm = {re.sub(r"[^A-Z0-9_]", "", s.upper()): s for s in spalten}
    fehlend = [p for p in PFLICHT if p not in norm]
    if fehlend:
        raise RuntimeError("Unerwartetes Dateiformat – es fehlen die Spalten "
                           f"{', '.join(fehlend)}. Gefunden: {', '.join(spalten[:25])}")

    def C(name: str) -> str:
        return '"' + norm[name].replace('"', '""') + '"' if name in norm else "NULL::VARCHAR"

    con.execute(f"""
        CREATE TEMP TABLE ev AS
        SELECT
          trim({C('FAHRT_BEZEICHNER')})                              AS fahrt,
          nullif(trim({C('BETREIBER_ABK')}), '')                     AS betreiber,
          nullif(trim({C('BETREIBER_NAME')}), '')                    AS betreiber_name,
          nullif(trim({C('PRODUKT_ID')}), '')                        AS produkt_roh,
          nullif(trim({C('LINIEN_ID')}), '')                         AS linien_id,
          nullif(trim({C('LINIEN_TEXT')}), '')                       AS linie,
          nullif(trim({C('VERKEHRSMITTEL_TEXT')}), '')               AS vm,
          lower(trim(coalesce({C('FAELLT_AUS_TF')}, ''))) = 'true'   AS ausfall,
          lower(trim(coalesce({C('DURCHFAHRT_TF')}, ''))) = 'true'   AS durchfahrt,
          lower(trim(coalesce({C('ZUSATZFAHRT_TF')}, ''))) = 'true'  AS zusatz,
          trim(coalesce({C('BPUIC')}, ''))                           AS bpuic,
          trim({C('HALTESTELLEN_NAME')})                             AS halt,
          try_strptime(trim({C('ANKUNFTSZEIT')}), {ZEITFORMATE})     AS an_soll,
          try_strptime(trim({C('AN_PROGNOSE')}), {ZEITFORMATE})      AS an_ist,
          upper(trim(coalesce({C('AN_PROGNOSE_STATUS')}, '')))       AS an_status,
          try_strptime(trim({C('ABFAHRTSZEIT')}), {ZEITFORMATE})     AS ab_soll,
          try_strptime(trim({C('AB_PROGNOSE')}), {ZEITFORMATE})      AS ab_ist,
          upper(trim(coalesce({C('AB_PROGNOSE_STATUS')}, '')))       AS ab_status
        FROM {quelle_sql}
        WHERE nullif(trim({C('FAHRT_BEZEICHNER')}), '') IS NOT NULL""")
    zeilen = con.execute("SELECT count(*) FROM ev").fetchone()[0]
    if zeilen == 0:
        raise RuntimeError("Die Datei enthält keine lesbaren Zeilen.")
    log(f"  · {zahl(zeilen)} Zeilen gelesen")

    # Welche Zeiten zählen? Standard: nur gemessene/geschätzte Ist-Zeiten.
    status = dict(con.execute("""
        SELECT s, count(*) FROM (
          SELECT an_status AS s FROM ev WHERE an_soll IS NOT NULL
          UNION ALL SELECT ab_status FROM ev WHERE ab_soll IS NOT NULL) GROUP BY s""").fetchall())
    alle_zeiten = sum(status.values()) or 1
    anteil_gemessen = sum(v for k, v in status.items() if k in GEMESSEN) / alle_zeiten
    erlaubt, basis = list(GEMESSEN), "gemessen"
    if mit_prognose or anteil_gemessen < 0.2:
        erlaubt += list(PROGNOSE)
        basis = "inkl. Prognosen"
    log(f"  · {anteil_gemessen:.0%} der Zeiten gemessen → Basis: {basis}")
    st = ", ".join(f"'{s}'" for s in erlaubt)

    con.execute(f"""
        CREATE TEMP TABLE ev2 AS
        SELECT *,
          (bpuic = '' OR bpuic LIKE '85%' OR lower(bpuic) LIKE 'ch:%') AS ch,
          coalesce(an_soll, ab_soll) AS t_soll,
          CASE WHEN NOT ausfall AND an_status IN ({st}) AND an_ist IS NOT NULL AND an_soll IS NOT NULL
               THEN date_diff('second', an_soll, an_ist) / 60.0 END AS an_vsp,
          CASE WHEN NOT ausfall AND ab_status IN ({st}) AND ab_ist IS NOT NULL AND ab_soll IS NOT NULL
               THEN date_diff('second', ab_soll, ab_ist) / 60.0 END AS ab_vsp
        FROM ev""")

    # Ein Eintrag pro Schweizer Halt (Durchfahrten und Auslandhalte zählen nicht)
    con.execute(f"""
        CREATE TEMP TABLE halte AS
        SELECT fahrt, halt, t_soll,
               any_value(an_soll) AS an_soll,
               bool_or(ausfall) AS ausfall,
               max(CASE WHEN abs(an_vsp) <= {MAX_PLAUSIBEL} THEN an_vsp END) AS an_vsp,
               max(CASE WHEN abs(greatest(an_vsp, ab_vsp)) <= {MAX_PLAUSIBEL}
                        THEN greatest(an_vsp, ab_vsp) END) AS vsp,
               bool_or(abs(greatest(an_vsp, ab_vsp)) > {MAX_PLAUSIBEL}) AS unplausibel
        FROM ev2 WHERE ch AND NOT durchfahrt
        GROUP BY fahrt, halt, t_soll""")

    con.execute(f"""
        CREATE TEMP TABLE fahrten AS
        SELECT fahrt,
               count(*)                                   AS halte,
               count(*) FILTER (WHERE ausfall)            AS halte_ausfall,
               count(vsp)                                 AS messpunkte,
               max(vsp)                                   AS max_vsp,
               arg_max(halt, vsp)                         AS max_halt,
               arg_max(t_soll, vsp)                       AS max_zeit,
               arg_max(an_vsp, an_soll)                   AS end_vsp,
               count(*) FILTER (WHERE vsp >= {SCHWELLE})  AS n_ueber,
               coalesce(bool_or(unplausibel), false)      AS unplausibel
        FROM halte GROUP BY fahrt""")

    con.execute("""
        CREATE TEMP TABLE attr AS
        SELECT fahrt,
               any_value(betreiber) AS betreiber, any_value(betreiber_name) AS betreiber_name,
               any_value(produkt_roh) AS produkt_roh, any_value(linie) AS linie,
               any_value(vm) AS vm, any_value(linien_id) AS linien_id, bool_or(zusatz) AS zusatz
        FROM ev GROUP BY fahrt""")

    # Route inkl. Auslandhalte (nur für die Anzeige «von → nach»)
    con.execute("""
        CREATE TEMP TABLE routen AS
        SELECT fahrt,
               arg_min(halt, t_soll) AS von, min(t_soll) AS start_soll,
               arg_max(halt, t_soll) AS nach, max(t_soll) AS ende_soll,
               bool_or(NOT ch) AS international
        FROM ev2 WHERE NOT durchfahrt AND t_soll IS NOT NULL GROUP BY fahrt""")

    con.execute("""
        CREATE TEMP TABLE f AS
        SELECT f.*, a.betreiber, a.betreiber_name, a.linie, a.vm, a.linien_id, a.zusatz,
               CASE WHEN a.produkt_roh IS NULL THEN 'Unbekannt'
                    ELSE upper(left(a.produkt_roh, 1)) || lower(substr(a.produkt_roh, 2)) END AS produkt,
               r.von, r.nach, r.start_soll, r.ende_soll,
               coalesce(r.international, false) AS international,
               (f.halte_ausfall = f.halte) AS komplett_ausfall,
               (f.halte_ausfall > 0 AND f.halte_ausfall < f.halte) AS teilausfall
        FROM fahrten f JOIN attr a USING (fahrt) LEFT JOIN routen r USING (fahrt)""")

    k = con.execute(f"""
        SELECT count(*), count(*) FILTER (WHERE messpunkte > 0),
               {', '.join(f'count(*) FILTER (WHERE max_vsp >= {s})' for s in STUFEN)},
               max(max_vsp),
               count(*) FILTER (WHERE komplett_ausfall),
               count(*) FILTER (WHERE teilausfall),
               count(*) FILTER (WHERE unplausibel)
        FROM f""").fetchone()
    kennzahlen = {
        "fahrten": k[0], "fahrten_gemessen": k[1],
        **{f"ab{s}": k[2 + i] for i, s in enumerate(STUFEN)},
        "max": minuten(k[2 + len(STUFEN)]) or 0,
        "ausfaelle": k[3 + len(STUFEN)], "teilausfaelle": k[4 + len(STUFEN)],
        "unplausibel": k[5 + len(STUFEN)],
    }

    produkte = [{"produkt": p, "fahrten": n, "gemessen": g, "ab60": a} for p, n, g, a in con.execute(f"""
        SELECT produkt, count(*), count(*) FILTER (WHERE messpunkte > 0),
               count(*) FILTER (WHERE max_vsp >= {SCHWELLE})
        FROM f GROUP BY 1 ORDER BY 2 DESC""").fetchall()]

    betreiber = [{"kurz": b, "name": n or b, "fahrten": alle, "ab60": a} for b, n, alle, a in con.execute(f"""
        WITH b AS (SELECT coalesce(betreiber, '?') AS b, any_value(betreiber_name) AS n, count(*) AS alle,
                          count(*) FILTER (WHERE max_vsp >= {SCHWELLE}) AS a
                   FROM f GROUP BY 1)
        SELECT b, n, alle, a FROM b WHERE a > 0 ORDER BY a DESC, b LIMIT 10""").fetchall()]

    hotspots = [{"halt": h, "fahrten": n, "max": minuten(m)} for h, n, m in con.execute(f"""
        SELECT halt, count(DISTINCT fahrt) AS n, max(vsp) FROM halte WHERE vsp >= {SCHWELLE}
        GROUP BY halt ORDER BY n DESC, 3 DESC, halt LIMIT 15""").fetchall()]

    stunden = [0] * 24
    for h, n in con.execute(f"""SELECT hour(max_zeit), count(*) FROM f
                                WHERE max_vsp >= {SCHWELLE} AND max_zeit IS NOT NULL GROUP BY 1""").fetchall():
        stunden[int(h) % 24] += n

    con.execute(f"""
        CREATE TEMP TABLE top AS
        SELECT * FROM f WHERE max_vsp >= {SCHWELLE}
        ORDER BY max_vsp DESC, fahrt LIMIT {MAX_LISTE}""")
    verlauf = defaultdict(list)
    for fahrt, halt, t, vsp, ausfall in con.execute("""
            SELECT fahrt, halt, t_soll, vsp, ausfall FROM halte
            WHERE fahrt IN (SELECT fahrt FROM top) AND t_soll IS NOT NULL
            ORDER BY fahrt, t_soll""").fetchall():
        verlauf[fahrt].append([halt, hhmm(t), minuten(vsp), "A" if ausfall else ""])

    liste = []
    spalten_top = [d[0] for d in con.execute("SELECT * FROM top LIMIT 0").description]
    for i, zeile in enumerate(con.execute("SELECT * FROM top ORDER BY max_vsp DESC, fahrt").fetchall()):
        t = dict(zip(spalten_top, zeile))
        produkt = t["produkt"]
        liste.append({
            "r": i + 1,
            "id": t["fahrt"],
            "linie": linie_anzeige(t["linie"], t["vm"], produkt),
            "nr": zugnummer(t["linien_id"], t["fahrt"], produkt),
            "produkt": produkt,
            "betreiber": t["betreiber"] or "?",
            "betreiber_name": t["betreiber_name"] or t["betreiber"] or "?",
            "von": t["von"], "nach": t["nach"],
            "ab": hhmm(t["start_soll"]), "an": hhmm(t["ende_soll"]),
            "max": minuten(t["max_vsp"]), "wo": t["max_halt"], "um": hhmm(t["max_zeit"]),
            "ende": minuten(t["end_vsp"]), "n60": t["n_ueber"],
            "int": bool(t["international"]), "teil": bool(t["teilausfall"]), "zusatz": bool(t["zusatz"]),
            "halte": verlauf.get(t["fahrt"], []),
        })

    tag = {
        "datum": datum.isoformat(),
        "titel": datum_lang(datum),
        "erstellt": jetzt_iso(),
        "quelle": {**quelle, "zeilen": zeilen},
        "basis": basis,
        "anteil_gemessen": round(anteil_gemessen, 3),
        "schwelle": SCHWELLE,
        "kennzahlen": kennzahlen,
        "produkte": produkte,
        "betreiber": betreiber,
        "hotspots": hotspots,
        "stunden": stunden,
        "status": {(s or "LEER"): n for s, n in sorted(status.items(), key=lambda x: -x[1])},
        "liste": liste,
    }
    tag["text"] = zusammenfassung(tag, vortag)
    tag["dauer_s"] = round(time.time() - t0, 1)
    con.close()
    return tag


def zusammenfassung(tag: dict, vortag: dict | None) -> str:
    k, n = tag["kennzahlen"], tag["kennzahlen"]["ab60"]
    if n == 0:
        return f"{tag['titel']}: Keine einzige Fahrt war eine Stunde oder länger verspätet."
    saetze = []
    vergleich = ""
    if vortag:
        diff = n - vortag["kennzahlen"]["ab60"]
        if diff > 0:
            vergleich = f" – {zahl(diff)} mehr als am Vortag"
        elif diff < 0:
            vergleich = f" – {zahl(-diff)} weniger als am Vortag"
        else:
            vergleich = " – gleich viele wie am Vortag"
    saetze.append(f"{tag['titel']}: {zahl(n)} {'Fahrt war' if n == 1 else 'Fahrten waren'} "
                  f"mindestens eine Stunde verspätet{vergleich}.")
    t = tag["liste"][0]
    route = f" von {t['von']} nach {t['nach']}" if t["von"] and t["nach"] and t["von"] != t["nach"] else ""
    saetze.append(f"Die grösste Verspätung hatte {t['linie']}{route}: {dauer(t['max'])}, "
                  f"gemessen in {t['wo']} (planmässig {t['um']} Uhr).")
    if tag["betreiber"]:
        b = tag["betreiber"][0]
        saetze.append(f"Am häufigsten betroffen: {b['kurz']} mit {zahl(b['ab60'])} "
                      f"{'Fahrt' if b['ab60'] == 1 else 'Fahrten'}.")
    if tag["hotspots"] and tag["hotspots"][0]["fahrten"] >= 2:
        h = tag["hotspots"][0]
        saetze.append(f"Brennpunkt war {h['halt']}: Dort waren {h['fahrten']} Fahrten über eine Stunde zu spät.")
    if k["fahrten_gemessen"]:
        saetze.append(f"Das ist etwa eine von {zahl(round(k['fahrten_gemessen'] / n))} Fahrten.")
    return " ".join(saetze)


# ---------------------------------------------------------------- Index

def lies(pfad: Path) -> dict:
    return json.loads(pfad.read_text(encoding="utf-8"))


def schreibe(pfad: Path, daten: dict) -> None:
    pfad.parent.mkdir(parents=True, exist_ok=True)
    tmp = pfad.with_suffix(".tmp")
    tmp.write_text(json.dumps(daten, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(pfad)


def baue_index(ausgabe: Path, demo: bool = False) -> dict:
    dateien = sorted((ausgabe / "tage").glob("*.json"))
    serie, letzte = [], []
    for i, f in enumerate(dateien):
        d = lies(f)
        k = d["kennzahlen"]
        top = d["liste"][0] if d.get("liste") else None
        serie.append({
            "datum": d["datum"], "ab60": k["ab60"], "ab120": k["ab120"], "max": k["max"],
            "fahrten": k["fahrten"], "ausfaelle": k["ausfaelle"], "basis": d.get("basis"),
            "top": {key: top[key] for key in ("linie", "von", "nach", "max", "wo")} if top else None,
        })
        if i >= len(dateien) - 30:
            letzte.append(d)

    linien, routen, halte = Counter(), defaultdict(Counter), Counter()
    for d in letzte:
        for t in d.get("liste", []):
            key = (t["betreiber"], t["linie"])
            linien[key] += 1
            routen[key][(t["von"], t["nach"])] += 1
            for h in {x[0] for x in t["halte"] if x[2] is not None and x[2] >= SCHWELLE}:
                halte[h] += 1

    index = {
        "app": "Plus 60",
        "version": 1,
        "demo": demo,
        "aktualisiert": jetzt_iso(),
        "schwelle": SCHWELLE,
        "quelle": {
            "name": "opentransportdata.swiss – Ist-Daten",
            "url": "https://data.opentransportdata.swiss/dataset/ist-daten-v2",
            "lizenz": "Open Data, Quellenangabe Pflicht",
        },
        "tage": serie,
        "zeitraum_30": ({"von": letzte[0]["datum"], "bis": letzte[-1]["datum"], "tage": len(letzte)}
                        if letzte else None),
        "linien_30": [{"betreiber": b, "linie": l, "anzahl": n,
                       "route": " – ".join(x for x in routen[(b, l)].most_common(1)[0][0] if x)}
                      for (b, l), n in linien.most_common(10)],
        "halte_30": [{"halt": h, "anzahl": n} for h, n in halte.most_common(10)],
    }
    schreibe(ausgabe / "index.json", index)
    return index


# ---------------------------------------------------------------- Ablauf

def heute_ch() -> dt.date:
    return dt.datetime.now(TZ).date()


def vortag_laden(ausgabe: Path, datum: dt.date) -> dict | None:
    f = ausgabe / "tage" / f"{(datum - dt.timedelta(days=1)).isoformat()}.json"
    return lies(f) if f.exists() else None


def tag_verarbeiten(datum: dt.date, ausgabe: Path, mit_prognose: bool, csv: Path | None = None) -> dict | None:
    log(f"\n{datum.isoformat()} · {datum_lang(datum)}")
    with tempfile.TemporaryDirectory(prefix="plus60-") as tmp:
        if csv:
            pfad, quelle = csv, {"datensatz": "lokal", "datei": csv.name, "url": None}
        else:
            quelle = finde_datei(datum)
            if not quelle:
                log("  – noch keine Datei veröffentlicht, nächster Versuch beim nächsten Lauf")
                return None
            log(f"  · Quelle: {quelle['datensatz']} / {quelle['datei']}")
            pfad = Path(tmp) / "istdaten.csv"
            herunterladen(quelle["url"], pfad)
            pfad = entpacken(pfad)
        tag = analysiere(pfad, datum, quelle, vortag_laden(ausgabe, datum), mit_prognose)
    schreibe(ausgabe / "tage" / f"{datum.isoformat()}.json", tag)
    k = tag["kennzahlen"]
    log(f"  ✓ {k['ab60']} Fahrten ≥ {SCHWELLE} Min. (max. {k['max']} Min.) · {tag['dauer_s']} s")
    return tag


def kurzbericht(neue: list[dict]) -> None:
    """Zusammenfassung für die Übersicht des GitHub-Actions-Laufs."""
    ziel = os.environ.get("GITHUB_STEP_SUMMARY")
    if not ziel or not neue:
        return
    zeilen = ["## Plus 60 – neue Tagesanalysen", ""]
    for t in neue:
        zeilen += [f"### {t['titel']}", "", t["text"], ""]
        if t["liste"]:
            zeilen += ["| # | Linie | Route | Max. | Wo |", "|---|---|---|---|---|"]
            for x in t["liste"][:5]:
                zeilen.append(f"| {x['r']} | {x['linie']} | {x['von']} – {x['nach']} | {x['max']} Min. | {x['wo']} |")
            zeilen.append("")
    with open(ziel, "a", encoding="utf-8") as f:
        f.write("\n".join(zeilen) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Plus 60 – tägliche Verspätungsanalyse Schweiz")
    ap.add_argument("--tage", type=int, help=f"so viele Tage rückwirkend prüfen "
                                             f"(Standard {STANDARD_TAGE}, beim Erstlauf {ERSTLAUF_TAGE})")
    ap.add_argument("--datum", help="nur diesen Tag rechnen (JJJJ-MM-TT), überschreibt vorhandene Daten")
    ap.add_argument("--csv", help="lokale Ist-Daten-Datei statt Download (zusammen mit --datum)")
    ap.add_argument("--ausgabe", default=str(STANDARD_AUSGABE), help="Zielordner für die JSON-Dateien")
    ap.add_argument("--mit-prognose", action="store_true", help="auch reine Prognosewerte mitzählen")
    ap.add_argument("--neu", action="store_true", help="schon berechnete Tage neu rechnen")
    ap.add_argument("--demo", action="store_true", help="Ausgabe als Beispieldaten markieren")
    args = ap.parse_args(argv)

    ausgabe = Path(args.ausgabe)
    (ausgabe / "tage").mkdir(parents=True, exist_ok=True)
    fehler, neue = [], []

    if args.csv:
        if not args.datum:
            ap.error("--csv braucht --datum")
        datum = dt.date.fromisoformat(args.datum)
        neue.append(tag_verarbeiten(datum, ausgabe, args.mit_prognose, Path(args.csv)))
    else:
        if args.datum:
            daten, ueberschreiben = [dt.date.fromisoformat(args.datum)], True
        else:
            vorhanden = any((ausgabe / "tage").glob("*.json"))
            n = args.tage or (STANDARD_TAGE if vorhanden else ERSTLAUF_TAGE)
            heute = heute_ch()
            daten = [heute - dt.timedelta(days=i) for i in range(n, 0, -1)]  # älteste zuerst
            ueberschreiben = args.neu
        for datum in daten:
            if (ausgabe / "tage" / f"{datum.isoformat()}.json").exists() and not ueberschreiben:
                continue
            try:
                tag = tag_verarbeiten(datum, ausgabe, args.mit_prognose)
                if tag:
                    neue.append(tag)
            except Exception as e:  # noqa: BLE001 – ein kaputter Tag soll die anderen nicht stoppen
                log(f"  ✗ Fehler: {e}")
                fehler.append(f"{datum.isoformat()}: {e}")

    index = baue_index(ausgabe, demo=args.demo)
    neue = [t for t in neue if t]
    log(f"\nFertig: {len(neue)} neue Tage, {len(index['tage'])} Tage total.")
    kurzbericht(neue)
    if fehler:
        log("\nProbleme:\n  " + "\n  ".join(fehler))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
