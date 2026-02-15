from __future__ import annotations

import csv
from pathlib import Path


CSV_FILE_ALIASES = {
    "単勝": "win",
    "複勝": "place",
    "枠連": "bracket_quinella",
    "馬連": "quinella",
    "ワイド": "quinella_place",
    "馬単": "exacta",
    "三連複": "trio",
    "三連単": "trifecta",
}


def _safe_file_name(name: str) -> str:
    if name in CSV_FILE_ALIASES:
        return CSV_FILE_ALIASES[name]
    return name.replace("/", "_").replace(" ", "_").lower()


def write_odds_csv_files(odds_data: dict[str, list[dict[str, str]]], output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written_files: list[Path] = []

    for bet_type, rows in odds_data.items():
        if not isinstance(rows, list):
            continue

        file_path = output_dir / f"{_safe_file_name(bet_type)}.csv"

        if not rows:
            file_path.write_text("", encoding="utf-8")
            written_files.append(file_path)
            continue

        fieldnames: list[str] = []
        seen: set[str] = set()
        for row in rows:
            for key in row.keys():
                if key not in seen:
                    seen.add(key)
                    fieldnames.append(key)

        with file_path.open("w", encoding="utf-8", newline="") as fp:
            writer = csv.DictWriter(fp, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)

        written_files.append(file_path)

    return written_files
