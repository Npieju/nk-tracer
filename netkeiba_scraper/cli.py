from __future__ import annotations

import argparse
import json
from pathlib import Path

from .csv_exporter import write_odds_csv_files
from .scraper import NetkeibaScraper


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="netkeiba race/odds scraper")
    parser.add_argument("--url", default=None, help="Race page URL")
    parser.add_argument("--url-file", default=None, help="Text file containing race URLs (one per line)")
    parser.add_argument("--output", default="race_data.json", help="Output JSON path")
    parser.add_argument("--csv-dir", default=None, help="Directory to export bet-type CSV files")
    parser.add_argument("--batch-output-dir", default="out/batch", help="Output root directory for --url-file mode")
    parser.add_argument("--indent", type=int, default=2, help="JSON indent")
    return parser


def _load_urls_from_file(url_file: str) -> list[str]:
    lines = Path(url_file).read_text(encoding="utf-8").splitlines()
    urls = []
    for line in lines:
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        urls.append(value)
    return urls


def _write_single_result(result: dict, output_path: Path, csv_dir: Path | None, indent: int) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=indent),
        encoding="utf-8",
    )
    print(f"saved: {output_path}")

    if csv_dir:
        written_files = write_odds_csv_files(result.get("odds", {}), csv_dir)
        for file_path in written_files:
            print(f"saved: {file_path}")

    odds_status = result.get("odds_status", {})
    for bet_type, status in odds_status.items():
        status_value = status.get("status", "unknown") if isinstance(status, dict) else "unknown"
        rows = status.get("rows", 0) if isinstance(status, dict) else 0
        message = status.get("message", "") if isinstance(status, dict) else ""
        print(f"odds_status[{bet_type}]: {status_value} rows={rows} {message}")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not args.url and not args.url_file:
        parser.error("--url または --url-file のどちらかを指定してください")
    if args.url and args.url_file:
        parser.error("--url と --url-file は同時に指定できません")

    scraper = NetkeibaScraper()

    if args.url:
        result = scraper.scrape(args.url)
        csv_dir = Path(args.csv_dir) if args.csv_dir else None
        _write_single_result(result, Path(args.output), csv_dir, args.indent)
        return

    urls = _load_urls_from_file(args.url_file)
    if not urls:
        parser.error("--url-file に有効なURLがありません")

    batch_root = Path(args.batch_output_dir)
    batch_root.mkdir(parents=True, exist_ok=True)
    seen_race_ids: dict[str, int] = {}

    for index, url in enumerate(urls, start=1):
        result = scraper.scrape(url)
        race_id_base = str(result.get("race_id") or f"race_{index:03d}")
        seen_race_ids[race_id_base] = seen_race_ids.get(race_id_base, 0) + 1
        suffix = seen_race_ids[race_id_base]
        race_id = race_id_base if suffix == 1 else f"{race_id_base}_{suffix:02d}"

        race_dir = batch_root / race_id
        output_path = race_dir / "race_data.json"
        csv_dir = race_dir / "csv"
        _write_single_result(result, output_path, csv_dir, args.indent)


if __name__ == "__main__":
    main()
