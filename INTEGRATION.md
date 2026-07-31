# Integrierter Wochenexport

Der Workflow `.github/workflows/weekly-register-bot.yml` läuft jeden Montag um 07:15 Uhr in `Europe/Zurich`.
Er verarbeitet die sieben Kalendertage bis einschliesslich Sonntag, speichert je Datum einen JSON-Bericht und erstellt eine gemeinsame Excel-Datei unter `exports/`.

Erforderliche GitHub-Secrets:

- `ZEFIX_USERNAME`
- `ZEFIX_PASSWORD`

Unter **Settings → Actions → General → Workflow permissions** muss **Read and write permissions** aktiviert sein.

Ein manueller Lauf ist über **Actions → Getränke-Handelsregister wöchentlich → Run workflow** möglich. Optional kann dort ein Enddatum angegeben werden.
