# Getränke-Handelsregister-Bot Schweiz

Der Bot prüft die täglichen SHAB/Zefix-Handelsregisterpublikationen und filtert Unternehmen aus der Getränkebranche. Er verarbeitet drei Ereignistypen:

- **Neueintragungen / Neugründungen** (`new`)
- **Mutationen** wie Zweck-, Sitz-, Firmen- oder Organänderungen (`mutation`)
- **Schliessungen**, Liquidationen, Konkurse und Löschungen (`closure`)

Zusätzlich speichert er öffentlich auffindbare Kontakte:

- Namen von Organpersonen beziehungsweise Vertretungsberechtigten aus strukturierten Registerdaten
- E-Mail-Adressen und Telefonnummern aus der Registerpublikation
- optional E-Mail und Telefon von einer in den Daten vorhandenen Firmenwebsite
- Quellenangaben je Kontaktart

## Wichtige Einschränkung bei Kontaktdaten

Zefix/SHAB enthält üblicherweise keine geschäftliche E-Mail-Adresse oder Telefonnummer. Der Bot erfindet keine Angaben und führt keine allgemeine Suchmaschinenabfrage durch. Website-Enrichment findet nur statt, wenn in den abgerufenen Daten bereits eine Website-URL enthalten ist. Fehlende Kontakte bleiben leer.

Die erfassten Personennamen sind häufig Organpersonen und nicht zwingend operative Verkaufs- oder Einkaufsansprechpartner.

## Installation

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Zefix-Zugangsdaten und einen identifizierbaren User-Agent in `.env` eintragen.

## Ausführen

```bash
# Standard: gestern, alle drei Ereignistypen
python bot.py

# Bestimmtes Datum
python bot.py --date 2026-07-28

# Nur Mutationen und Schliessungen
python bot.py --date 2026-07-28 --events mutation,closure

# Maschinenlesbare Ausgabe
python bot.py --date 2026-07-28 --json
```

## Datenbankfelder

Die SQLite-Tabelle `matches` enthält zusätzlich zu Firmen- und Publikationsdaten:

- `event_type`
- `contact_names` als JSON-Liste
- `emails` als JSON-Liste
- `phones` als JSON-Liste
- `website`
- `contact_sources` als JSON-Objekt

Eine vorhandene Datenbank der ersten Version wird beim Start automatisch um diese Spalten erweitert.

## Konfiguration

```env
EVENT_TYPES=new,mutation,closure
ENRICH_CONTACTS=true
MAX_CONTACT_PAGES=3
```

Mit `ENRICH_CONTACTS=false` werden keine Firmenwebsites aufgerufen. Die Anzahl besuchter Seiten pro Website ist bewusst begrenzt. `robots.txt`, Website-Nutzungsbedingungen und Abruflimiten sind beim produktiven Betrieb zusätzlich zu berücksichtigen.

## Cron-Beispiel

```cron
15 7 * * * cd /pfad/ch_beverage_registry_bot && .venv/bin/python bot.py >> bot.log 2>&1
```

## Trefferqualität

Die Branchenklassifikation verwendet mehrsprachige Begriffe zu Getränken, Bier, Wein, Spirituosen, Mineralwasser, Säften, Kaffee und Tee. Für eine präzisere produktive Klassifikation empfiehlt sich eine zusätzliche NOGA/BUR-Stufe und eine manuelle Review-Liste für unsichere Treffer.

## Datenschutz

Nur öffentlich publizierte geschäftliche Daten zweckgebunden speichern. Vor Direktmarketing sind insbesondere Datenschutz-, Fernmelde- und Lauterkeitsvorgaben sowie Opt-out-/Sperrlistenprozesse zu prüfen. Personen- und Kontaktdaten sollten mit Aufbewahrungsfrist, Quellenbeleg und Löschprozess geführt werden.

# Vollständig online mit GitHub Actions

Das Repository sollte **privat** sein. Der Workflow läuft täglich um **07:15 Uhr in der Zeitzone Europe/Zurich** und kann zusätzlich im Tab **Actions** manuell gestartet werden. GitHub Actions unterstützt geplante Workflows mit Cron-Ausdruck und Zeitzonenangabe.

## 1. Neues privates Repository erstellen

1. Auf GitHub **New repository** wählen.
2. Einen Namen wie `ch-beverage-register-bot` vergeben.
3. **Private** auswählen.
4. Das Repository erstellen, ohne zusätzliche README oder `.gitignore`.

## 2. Projekt hochladen

Im entpackten Projektordner ausführen:

```bash
git init
git add .
git commit -m "Getränke-Handelsregister-Bot einrichten"
git branch -M main
git remote add origin https://github.com/DEIN-NAME/ch-beverage-register-bot.git
git push -u origin main
```

Alternativ können alle Dateien über **Add file → Upload files** hochgeladen werden. Der versteckte Ordner `.github/workflows` muss ebenfalls enthalten sein.

## 3. Optionale GitHub-Secrets anlegen

Im Repository **Settings → Secrets and variables → Actions → New repository secret** öffnen und anlegen:

| Secret | Pflicht | Inhalt |
|---|---:|---|
| `TELEGRAM_BOT_TOKEN` | nein | Token des Telegram-Bots |
| `TELEGRAM_CHAT_ID` | nein | Ziel-Chat oder Kanal |

Für den öffentlichen Zefix-/SHAB-Abruf sind keine Zefix-Zugangsdaten erforderlich. Telegram-Secrets gehören nicht in `.env` und nicht in den Quellcode.

## 4. Schreibrechte für den Workflow erlauben

Unter **Settings → Actions → General → Workflow permissions**:

1. **Read and write permissions** wählen.
2. **Save** anklicken.

Der Workflow schreibt die aktualisierte SQLite-Datenbank und den Tagesbericht zurück in das private Repository. Dadurch bleiben bereits verarbeitete Publikationen bekannt und werden nicht erneut gemeldet.

## 5. Ersten Test starten

1. Repository öffnen.
2. Tab **Actions** wählen.
3. Workflow **Getränke-Handelsregister täglich** öffnen.
4. **Run workflow** wählen.
5. Optional ein Datum wie `2026-07-28` eintragen.
6. Mit **Run workflow** starten.

Nach dem Lauf befinden sich:

- die dauerhafte Datenbank unter `data/registry_bot.sqlite3`,
- der JSON-Tagesbericht unter `reports/treffer-YYYY-MM-DD.json`,
- ein 30 Tage verfügbares Download-Artefakt beim jeweiligen Workflow-Lauf.

## Zeitplan ändern

In `.github/workflows/daily-register-bot.yml`:

```yaml
schedule:
  - cron: "15 7 * * *"
    timezone: "Europe/Zurich"
```

Beispiele:

- `0 6 * * *` – täglich 06:00 Uhr
- `30 8 * * 1-5` – Montag bis Freitag 08:30 Uhr

GitHub kann geplante Läufe bei hoher Auslastung etwas verzögert starten. Geplante Workflows laufen nur vom Standardbranch.

## Datenschutz und Repository-Grösse

Das Repository privat halten. Die SQLite-Datei kann Namen und geschäftliche Kontaktdaten enthalten. Alte JSON-Berichte bei Bedarf regelmässig löschen oder eine Aufbewahrungsroutine ergänzen. SQLite-Dateien wachsen mit der Zeit; für einen grösseren produktiven Betrieb ist später eine externe PostgreSQL-Datenbank sinnvoller.
