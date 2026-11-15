# Plus 60

Die grössten Verspätungen im Schweizer ÖV, jeden Tag automatisch ausgewertet.

Jeden Morgen holt GitHub Actions die Ist-Daten des Vortags von opentransportdata.swiss (alle Halte aller Fahrten, rund 600 MB), sucht jede Zugfahrt, die an einem Schweizer Halt **mindestens 60 Minuten** zu spät war, und aktualisiert das Dashboard.

## So läuft es ab

1. **Um ca. 06:23 Uhr** startet der Workflow «Tägliche Analyse» (Reserveläufe um ca. 10:23 und 16:23 Uhr, falls die Datei später erscheint).
2. `pipeline/analyse.py` liest den Download-Link von der Datensatz-Seite, lädt die Datei herunter und wertet sie mit DuckDB aus. Das dauert rund 15 Sekunden pro Tag.
3. Das Ergebnis landet in `docs/data/tage/JJJJ-MM-TT.json` und `docs/data/index.json` und wird automatisch gespeichert (Commit).
4. Das Dashboard `docs/index.html` wird auf GitHub Pages veröffentlicht.

Fehlt ein Tag, holt der nächste Lauf ihn nach, bis 7 Tage zurück. Schlägt ein Lauf fehl, schickt GitHub eine E-Mail.

## Einrichten (einmalig, ca. 5 Minuten)

1. Auf GitHub ein neues **öffentliches** Repository anlegen, z. B. `plus60`.
2. Diesen Ordner hochladen, **inklusive** des versteckten Ordners `.github` (auf dem Mac im Finder mit `Cmd + Shift + .` sichtbar machen).
3. Im Repository: **Settings → Pages → Build and deployment → Source: «GitHub Actions»**.
4. **Actions → «Tägliche Analyse» → Run workflow**. Der erste Lauf holt automatisch die letzten 30 Tage (ca. 10 Minuten).

Danach ist das Dashboard unter `https://DEIN-NAME.github.io/plus60/` erreichbar und aktualisiert sich selbst.

## Lokal ausprobieren

```bash
pip install -r pipeline/requirements.txt
python tests/test_analyse.py            # Tests mit synthetischen Daten, ohne Internet
python pipeline/analyse.py --tage 3     # echte Daten der letzten 3 Tage holen
python -m http.server -d docs 8000      # Dashboard: http://localhost:8000
```

Weitere Optionen: `python pipeline/analyse.py --help`

## Methode

- **Verspätung** = tatsächliche minus geplante Ankunfts- bzw. Abfahrtszeit, in ganzen Minuten. Pro Fahrt zählt die grösste Verspätung an einem Schweizer Halt.
- **Nur Züge.** Bei Bussen und Trams entstehen Werte über einer Stunde fast immer durch falsch zugeordnete Fahrten (ein Bus fährt eine spätere Fahrt, die Daten buchen sie auf eine frühere). Mit `--alle-verkehrsmittel` lassen sie sich trotzdem auswerten.
- **Gezählt** werden Ist-Zeiten mit Status «REAL» (gemessen) und «GESCHAETZT». Reine Prognosen zählen nicht, ausser an Tagen, an denen weniger als 20 % der Zeiten gemessen sind (das Dashboard weist darauf hin).
- **Bestätigt durch Nachbarhalte.** Eine Verspätung zählt nur, wenn der Halt davor oder danach (auch im Ausland) höchstens 40 Minuten weniger zeigt. Typische Messfehler, etwa +181 Min. an der Endstation nach durchgehend +5 Min., fallen so weg. Im Dashboard stehen sie mit «?» im Verlauf.
- **Nicht gezählt**: Durchfahrten ohne Halt, Halte im Ausland (der Datensatz v2 enthält sie, sie dienen nur für die Anzeige der Route), ausgefallene Halte und Abweichungen über 12 Stunden (Datenfehler).
- **Ausgefallen** = die Fahrt ist an allen Schweizer Halten als ausgefallen gemeldet.
- Pro Tag werden die 300 grössten Verspätungen mit ihrem Verlauf gespeichert; die Kennzahlen zählen alle Fahrten.

## Anpassen

| Was | Wo |
|---|---|
| Schwelle (60 Min.) | `SCHWELLE` in `pipeline/analyse.py` |
| Prognosen mitzählen | Option `--mit-prognose` |
| Toleranz für Nachbarhalte (40 Min.) | `TOLERANZ` in `pipeline/analyse.py` |
| Alles neu rechnen | Actions → Run workflow → Häkchen «neu rechnen» |
| Laufzeiten | `cron` in `.github/workflows/taeglich.yml` (in UTC) |
| Aussehen | `docs/index.html` (Farben als Variablen ganz oben) |

## Daten und Lizenz

Quelle: [opentransportdata.swiss, Ist-Daten](https://data.opentransportdata.swiss/dataset/ist-daten-v2), Open Data mit Pflicht zur Quellenangabe. Die Quelle steht im Dashboard. Ältere Tage gibt es im [Archiv](https://archive.opentransportdata.swiss/) als Monats-ZIP.

Unabhängige Auswertung offener Daten, ohne Verbindung zu den Transportunternehmen.
