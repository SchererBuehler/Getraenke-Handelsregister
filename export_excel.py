from __future__ import annotations

import argparse
import html
import json
from datetime import date
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter


REPORTS_DIR = Path("reports")
EXPORTS_DIR = Path("exports")


def repair_encoding(value: str) -> str:
    """Repariert typische fehlerhaft dekodierte UTF-8-Texte."""
    if not value or not any(marker in value for marker in ("Ã", "Â", "â€")):
        return value

    try:
        return value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value


def clean_text(value: Any) -> str:
    if value is None:
        return ""

    if isinstance(value, (list, tuple, set)):
        return ", ".join(clean_text(item) for item in value)

    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)

    return repair_encoding(html.unescape(str(value))).strip()


def get_nested(data: dict[str, Any], *keys: str) -> Any:
    current: Any = data

    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)

    return current


def normalize_uid(value: str) -> str:
    uid = "".join(character for character in value if character.isalnum())

    if uid.startswith("CHE") and len(uid) == 12:
        return f"{uid[:3]}-{uid[3:6]}.{uid[6:9]}.{uid[9:12]}"

    return value


def extract_row(entry: dict[str, Any]) -> list[Any]:
    company = get_nested(entry, "raw", "companyShort") or {}
    publication = get_nested(entry, "raw", "sogcPublication") or {}

    mutation_types = publication.get("mutationTypes") or []
    mutation_keys = [
        mutation.get("key", "")
        for mutation in mutation_types
        if isinstance(mutation, dict)
    ]

    return [
        clean_text(entry.get("publication_date")),
        clean_text(entry.get("event_type")),
        clean_text(entry.get("company_name") or company.get("name")),
        normalize_uid(clean_text(entry.get("uid") or company.get("uid"))),
        clean_text(
            entry.get("canton")
            or publication.get("registryOfCommerceCanton")
        ),
        clean_text(entry.get("locality") or company.get("legalSeat")),
        clean_text(get_nested(company, "legalForm", "name", "de")),
        clean_text(company.get("status")),
        clean_text(entry.get("categories", [])),
        entry.get("confidence", ""),
        clean_text(entry.get("contact_names", [])),
        clean_text(entry.get("emails", [])),
        clean_text(entry.get("phones", [])),
        clean_text(entry.get("website")),
        clean_text(mutation_keys),
        clean_text(publication.get("message") or entry.get("purpose")),
        clean_text(entry.get("publication_id")),
    ]


def create_excel(report_path: Path, excel_path: Path) -> int:
    with report_path.open("r", encoding="utf-8") as file:
        records = json.load(file)

    if not isinstance(records, list):
        raise ValueError("Der JSON-Bericht muss eine Liste enthalten.")

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Treffer"

    headers = [
        "Publikationsdatum",
        "Ereignistyp",
        "Firma",
        "UID",
        "Kanton",
        "Ort",
        "Rechtsform",
        "Status",
        "Kategorien",
        "Konfidenz",
        "Kontaktpersonen",
        "E-Mail-Adressen",
        "Telefonnummern",
        "Website",
        "Mutationsarten",
        "Publikationstext",
        "Publikations-ID",
    ]

    sheet.append(headers)

    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(
            horizontal="center",
            vertical="center",
        )

    for record in records:
        if isinstance(record, dict):
            sheet.append(extract_row(record))

    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions

    widths = {
        1: 18,
        2: 16,
        3: 36,
        4: 20,
        5: 10,
        6: 24,
        7: 34,
        8: 14,
        9: 25,
        10: 12,
        11: 35,
        12: 35,
        13: 25,
        14: 35,
        15: 30,
        16: 100,
        17: 28,
    }

    for number, width in widths.items():
        sheet.column_dimensions[get_column_letter(number)].width = width

    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(
                vertical="top",
                wrap_text=True,
            )

    summary = workbook.create_sheet("Zusammenfassung")
    summary.append(["Kennzahl", "Wert"])
    summary.append(["Anzahl Treffer", len(records)])

    event_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}

    for record in records:
        if not isinstance(record, dict):
            continue

        event_type = clean_text(record.get("event_type")) or "unbekannt"
        event_counts[event_type] = event_counts.get(event_type, 0) + 1

        categories = record.get("categories") or []
        if isinstance(categories, list):
            for category in categories:
                category = clean_text(category)
                if category:
                    category_counts[category] = (
                        category_counts.get(category, 0) + 1
                    )

    summary.append([])
    summary.append(["Ereignistyp", "Anzahl"])

    for event_type, count in sorted(event_counts.items()):
        summary.append([event_type, count])

    summary.append([])
    summary.append(["Kategorie", "Anzahl"])

    for category, count in sorted(
        category_counts.items(),
        key=lambda item: (-item[1], item[0]),
    ):
        summary.append([category, count])

    for cell in summary[1]:
        cell.font = Font(bold=True)

    summary.column_dimensions["A"].width = 30
    summary.column_dimensions["B"].width = 15

    excel_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(excel_path)

    return len(records)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="JSON-Tagesbericht als Excel-Datei exportieren."
    )
    parser.add_argument(
        "--date",
        required=True,
        help="Berichtsdatum im Format YYYY-MM-DD",
    )
    args = parser.parse_args()

    try:
        date.fromisoformat(args.date)
    except ValueError as exc:
        raise SystemExit(
            "--date muss das Format YYYY-MM-DD haben."
        ) from exc

    report_path = REPORTS_DIR / f"treffer-{args.date}.json"
    excel_path = EXPORTS_DIR / f"treffer-{args.date}.xlsx"

    if not report_path.exists():
        raise SystemExit(f"JSON-Bericht nicht gefunden: {report_path}")

    count = create_excel(report_path, excel_path)

    print(f"Excel-Datei erstellt: {excel_path}")
    print(f"Anzahl exportierter Treffer: {count}")


if __name__ == "__main__":
    main()
