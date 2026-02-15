from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import re
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup


BET_TYPES = ["単勝", "複勝", "枠連", "馬連", "ワイド", "馬単", "三連複", "三連単"]
ODDS_TYPE_MAP = {
    "単勝": "b1",
    "複勝": "b1",
    "枠連": "b2",
    "馬連": "b4",
    "ワイド": "b5",
    "馬単": "b6",
    "三連複": "b7",
    "三連単": "b8",
}
API_ODDS_TYPE_MAP = {
    "単勝": "1",
    "複勝": "2",
    "枠連": "3",
    "馬連": "4",
    "ワイド": "5",
    "馬単": "6",
    "三連複": "7",
    "三連単": "8",
}


@dataclass
class ScrapeOptions:
    timeout: int = 20
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )


class NetkeibaScraper:
    def __init__(self, options: ScrapeOptions | None = None) -> None:
        self.options = options or ScrapeOptions()
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": self.options.user_agent,
                "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
            }
        )

    def scrape(self, race_url: str) -> dict[str, Any]:
        html = self._fetch_html(race_url)
        soup = BeautifulSoup(html, "lxml")

        race_id = self._extract_race_id(race_url)
        race_date = self._extract_race_date(race_id)
        race_name = self._extract_race_name(soup)
        entries = self._extract_race_entries(soup)
        odds_links = self._discover_odds_links(soup, race_url)
        all_urls = dict(odds_links)

        odds: dict[str, Any] = {bet_type: [] for bet_type in BET_TYPES}
        odds_status: dict[str, Any] = {}

        for bet_type in BET_TYPES:
            try:
                rows, urls = self._collect_full_odds_for_bet_type(race_id, bet_type, entries)
                odds[bet_type] = rows
                for key, value in urls.items():
                    all_urls.setdefault(key, value)
                odds_status[bet_type] = self._build_odds_status(bet_type, rows, urls, race_date)
            except Exception as exc:  # noqa: BLE001
                failed_url = self._build_odds_type_url(race_id, bet_type) or self._build_abroad_type_url(race_id, bet_type)
                odds[bet_type] = [{"error": str(exc), "source_url": failed_url or ""}]
                odds_status[bet_type] = {
                    "status": "error",
                    "rows": 0,
                    "message": str(exc),
                    "source_url": failed_url,
                }

        return {
            "race_url": race_url,
            "race_id": race_id,
            "race_name": race_name,
            "race_date": race_date,
            "entries": entries,
            "odds": odds,
            "odds_status": odds_status,
            "odds_links": all_urls,
        }

    def _build_odds_status(
        self,
        bet_type: str,
        rows: list[dict[str, str]],
        urls: dict[str, str],
        race_date: str | None,
    ) -> dict[str, Any]:
        source_url = urls.get(bet_type)
        race_date_hint = self._build_future_race_hint(race_date)
        if not rows:
            api_reason = self._fetch_jra_odds_reason(source_url, bet_type)
            message = f"{bet_type}のオッズを取得できませんでした"
            if api_reason:
                message = f"{message} (api_reason: {api_reason})"
            if race_date_hint:
                message = f"{message} ({race_date_hint})"
            return {
                "status": "missing",
                "rows": 0,
                "message": message,
                "source_url": source_url,
            }

        has_numeric_odds = any(self._has_available_odds(row.get("オッズ", "")) for row in rows)
        if has_numeric_odds:
            return {
                "status": "ok",
                "rows": len(rows),
                "message": f"{bet_type}のオッズを取得しました",
                "source_url": source_url,
            }

        api_reason = self._fetch_jra_odds_reason(source_url, bet_type)
        message = f"{bet_type}は発売前または未更新の可能性があります"
        if api_reason:
            message = f"{message} (api_reason: {api_reason})"
        if race_date_hint:
            message = f"{message} ({race_date_hint})"
        return {
            "status": "unavailable",
            "rows": len(rows),
            "message": message,
            "source_url": source_url,
        }

    @staticmethod
    def _extract_race_date(race_id: str | None) -> str | None:
        if not race_id:
            return None
        digits = "".join(ch for ch in race_id if ch.isdigit())
        if len(digits) < 8:
            return None
        yyyymmdd = digits[:8]
        try:
            date_obj = datetime.strptime(yyyymmdd, "%Y%m%d")
            return date_obj.strftime("%Y-%m-%d")
        except ValueError:
            return None

    @staticmethod
    def _build_future_race_hint(race_date: str | None) -> str | None:
        if not race_date:
            return None
        try:
            race_dt = datetime.strptime(race_date, "%Y-%m-%d")
            today = datetime.now()
            if race_dt.date() > today.date():
                return f"race_date={race_date} は未来日付"
        except ValueError:
            return None
        return None

    def _fetch_jra_odds_reason(self, source_url: str | None, bet_type: str) -> str | None:
        race_id = self._extract_race_id(source_url or "")
        if not race_id:
            return None
        odds_type = API_ODDS_TYPE_MAP.get(bet_type)
        if not odds_type:
            return None
        try:
            payload = self._fetch_jra_odds_payload(race_id, odds_type, source_url)
            if not isinstance(payload, dict):
                return None
            data = payload.get("data")
            if isinstance(data, dict):
                odds = data.get("odds")
                if isinstance(odds, dict):
                    typed = odds.get(odds_type)
                    if isinstance(typed, dict) and typed:
                        return None
            reason = payload.get("reason")
            if reason:
                return str(reason)
            status = payload.get("status")
            return str(status) if status else None
        except Exception:  # noqa: BLE001
            return None

    def _fetch_jra_odds_payload(
        self,
        race_id: str,
        api_odds_type: str,
        referer_url: str | None,
    ) -> dict[str, Any] | None:
        api_url = "https://race.netkeiba.com/api/api_get_jra_odds.html"
        response = self.session.get(
            api_url,
            params={
                "pid": "api_get_jra_odds",
                "input": "UTF-8",
                "output": "json",
                "race_id": race_id,
                "type": api_odds_type,
                "action": "init",
                "sort": "odds",
                "compress": "0",
            },
            headers={"Referer": referer_url or ""},
            timeout=self.options.timeout,
        )
        response.raise_for_status()
        payload = json.loads(response.text)
        return payload if isinstance(payload, dict) else None

    def _extract_odds_rows_from_api_payload(
        self,
        payload: dict[str, Any],
        bet_type: str,
        entries: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        api_type = API_ODDS_TYPE_MAP.get(bet_type)
        if not api_type:
            return []

        data = payload.get("data")
        if not isinstance(data, dict):
            return []
        odds = data.get("odds")
        if not isinstance(odds, dict):
            return []
        typed_odds = odds.get(api_type)
        if not isinstance(typed_odds, dict) or not typed_odds:
            return []

        horse_name_by_no: dict[str, str] = {}
        for row in entries:
            no = self._extract_horse_number_from_entry_row(row)
            if no.isdigit():
                horse_name_by_no[no] = self._extract_horse_name_from_entry_row(row)

        rows: list[dict[str, str]] = []

        if bet_type in {"単勝", "複勝"}:
            for horse_no_key, values in typed_odds.items():
                horse_no = str(int(horse_no_key)) if str(horse_no_key).isdigit() else str(horse_no_key)
                if not isinstance(values, list) or not values:
                    continue
                odds_value = str(values[0]).strip() if values[0] is not None else ""
                if bet_type == "複勝" and len(values) >= 2 and values[1] is not None and str(values[1]).strip() not in {"", "0"}:
                    odds_value = f"{str(values[0]).strip()} - {str(values[1]).strip()}"
                rows.append(
                    {
                        "馬番": horse_no,
                        "馬名": horse_name_by_no.get(horse_no, ""),
                        "オッズ": self._normalize_odds_value(odds_value),
                    }
                )
            rows.sort(key=lambda row: int(row.get("馬番", "9999")) if row.get("馬番", "").isdigit() else 9999)
            return rows

        combo_size = 3 if bet_type in {"三連複", "三連単"} else 2
        for combo_key, values in typed_odds.items():
            if not isinstance(values, list) or not values:
                continue
            combo_str = str(combo_key)
            parts = [combo_str[i : i + 2] for i in range(0, len(combo_str), 2)]
            if len(parts) != combo_size or not all(part.isdigit() for part in parts):
                continue
            combo = "-".join(str(int(part)) for part in parts)
            odds_value = str(values[0]).strip() if values[0] is not None else ""
            if bet_type == "ワイド" and len(values) >= 2 and values[1] is not None and str(values[1]).strip() not in {"", "0"}:
                odds_value = f"{str(values[0]).strip()} - {str(values[1]).strip()}"
            rows.append(
                {
                    "組み合わせ": combo,
                    "オッズ": self._normalize_odds_value(odds_value),
                }
            )

        rows.sort(key=lambda row: self._combo_sort_key(row.get("組み合わせ", "")))
        return rows

    @staticmethod
    def _parse_numeric_odds(value: str) -> float | None:
        text = str(value).strip()
        if not text or text in {"-", "--", "---.-"}:
            return None
        text = text.replace(",", "")
        if "-" in text:
            return None
        try:
            return float(text)
        except ValueError:
            return None

    @staticmethod
    def _has_available_odds(value: str) -> bool:
        text = str(value).strip()
        if not text or text in {"-", "--", "---.-"}:
            return False
        if NetkeibaScraper._parse_numeric_odds(text) is not None:
            return True
        return bool(re.match(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*-\s*([0-9]+(?:\.[0-9]+)?)\s*$", text))

    def _collect_full_odds_for_bet_type(
        self,
        race_id: str | None,
        bet_type: str,
        entries: list[dict[str, str]],
    ) -> tuple[list[dict[str, str]], dict[str, str]]:
        urls: dict[str, str] = {}

        type_url = self._build_odds_type_url(race_id, bet_type)
        abroad_url = self._build_abroad_type_url(race_id, bet_type)

        if not type_url and not abroad_url:
            return [], urls

        primary_url = type_url or abroad_url
        urls[bet_type] = primary_url

        if race_id:
            api_type = API_ODDS_TYPE_MAP.get(bet_type)
            if api_type:
                try:
                    payload = self._fetch_jra_odds_payload(race_id, api_type, primary_url)
                    api_rows = self._extract_odds_rows_from_api_payload(payload or {}, bet_type, entries)
                    if api_rows:
                        urls[f"{bet_type}_api"] = "https://race.netkeiba.com/api/api_get_jra_odds.html"
                        return api_rows, urls
                except Exception:  # noqa: BLE001
                    pass

        if bet_type in {"単勝", "複勝"}:
            html = self._fetch_html(primary_url)
            soup = BeautifulSoup(html, "lxml")
            extracted = self._extract_odds_by_bet_type(soup)
            rows = extracted.get(bet_type, [])
            if not rows:
                fallback = self._extract_win_place_by_table_position(soup)
                rows = fallback.get(bet_type, [])
            if rows:
                return rows, urls
            if abroad_url and abroad_url != primary_url:
                fallback_html = self._fetch_html(abroad_url)
                fallback_soup = BeautifulSoup(fallback_html, "lxml")
                fallback_extracted = self._extract_odds_by_bet_type(fallback_soup)
                fallback_rows = fallback_extracted.get(bet_type, [])
                if not fallback_rows:
                    fallback_rows = self._extract_win_place_by_table_position(fallback_soup).get(bet_type, [])
                if fallback_rows:
                    urls[f"{bet_type}_fallback"] = abroad_url
                    return fallback_rows, urls
            return rows, urls

        if bet_type in {"三連複", "三連単"}:
            rows, jiku_urls = self._collect_triple_full_odds(primary_url, bet_type, entries)
            urls.update(jiku_urls)
            if rows:
                return rows, urls
            if abroad_url and abroad_url != primary_url:
                fallback_rows, fallback_jiku_urls = self._collect_triple_full_odds(abroad_url, bet_type, entries)
                for key, value in fallback_jiku_urls.items():
                    urls[f"fallback_{key}"] = value
                if fallback_rows:
                    urls[f"{bet_type}_fallback"] = abroad_url
                    return fallback_rows, urls
            return rows, urls

        html = self._fetch_html(primary_url)
        soup = BeautifulSoup(html, "lxml")
        rows = self._extract_cart_items(soup, bet_type)
        if rows:
            return rows, urls
        if abroad_url and abroad_url != primary_url:
            fallback_html = self._fetch_html(abroad_url)
            fallback_soup = BeautifulSoup(fallback_html, "lxml")
            fallback_rows = self._extract_cart_items(fallback_soup, bet_type)
            if fallback_rows:
                urls[f"{bet_type}_fallback"] = abroad_url
                return fallback_rows, urls
        return rows, urls

    @staticmethod
    def _build_odds_index_url(race_id: str | None) -> str | None:
        if not race_id:
            return None
        return f"https://race.netkeiba.com/odds/abroad.html?race_id={race_id}"

    @staticmethod
    def _build_odds_type_url(race_id: str | None, bet_type: str) -> str | None:
        if not race_id:
            return None
        odds_type = ODDS_TYPE_MAP.get(bet_type)
        if not odds_type:
            return None
        return f"https://race.netkeiba.com/odds/index.html?type={odds_type}&race_id={race_id}"

    @staticmethod
    def _build_abroad_type_url(race_id: str | None, bet_type: str) -> str | None:
        if not race_id:
            return None
        odds_type = ODDS_TYPE_MAP.get(bet_type)
        if not odds_type:
            return None
        return f"https://race.netkeiba.com/odds/abroad.html?type={odds_type}&race_id={race_id}"

    def _collect_triple_full_odds(
        self,
        abroad_url: str,
        bet_type: str,
        entries: list[dict[str, str]],
    ) -> tuple[list[dict[str, str]], dict[str, str]]:
        html = self._fetch_html(abroad_url)
        soup = BeautifulSoup(html, "lxml")

        jiku_values = self._extract_jiku_values(soup)
        if not jiku_values:
            jiku_values = self._extract_horse_numbers_from_entries(entries)

        all_rows: dict[str, dict[str, str]] = {}
        jiku_urls: dict[str, str] = {}

        for jiku in jiku_values:
            jiku_url = f"{abroad_url}&jiku={jiku}"
            jiku_urls[f"{bet_type}_jiku_{jiku}"] = jiku_url
            jiku_html = self._fetch_html(jiku_url)
            jiku_soup = BeautifulSoup(jiku_html, "lxml")
            for row in self._extract_cart_items(jiku_soup, bet_type, include_key=True):
                row_key = row.pop("_cart_item", "")
                if not row_key:
                    continue
                all_rows[row_key] = row

        rows = list(all_rows.values())
        rows.sort(key=lambda row: self._combo_sort_key(row.get("組み合わせ", "")))
        return rows, jiku_urls

    @staticmethod
    def _extract_jiku_values(soup: BeautifulSoup) -> list[str]:
        values: list[str] = []
        for option in soup.select("option[value]"):
            value = option.get("value", "").strip()
            if value.isdigit() and value not in values:
                values.append(value)
        return values

    @staticmethod
    def _extract_horse_numbers_from_entries(entries: list[dict[str, str]]) -> list[str]:
        values: list[str] = []
        for row in entries:
            value = NetkeibaScraper._extract_horse_number_from_entry_row(row)
            if value.isdigit() and value not in values:
                values.append(value)
        return values

    @staticmethod
    def _extract_horse_number_from_entry_row(row: dict[str, str]) -> str:
        normalized = {key.replace(" ", ""): str(value).strip() for key, value in row.items()}
        for key in ["馬番", "col_2", "col_1"]:
            value = normalized.get(key, "")
            if value.isdigit():
                return str(int(value))
        return ""

    @staticmethod
    def _extract_horse_name_from_entry_row(row: dict[str, str]) -> str:
        normalized = {key.replace(" ", ""): str(value).strip() for key, value in row.items()}
        for key in ["馬名", "col_4", "col_3"]:
            value = normalized.get(key, "")
            if value:
                return value
        return ""

    def _extract_cart_items(
        self,
        soup: BeautifulSoup,
        bet_type: str,
        include_key: bool = False,
    ) -> list[dict[str, str]]:
        expected_count = 2 if bet_type in {"枠連", "馬連", "ワイド", "馬単"} else 3
        rows: list[dict[str, str]] = []

        for cell in soup.select("td[cart-item]"):
            cart_item = cell.get("cart-item", "")
            numbers = re.findall(r"_(\d+)", cart_item)
            combo_numbers = numbers[-expected_count:]
            if len(combo_numbers) != expected_count:
                continue

            odds_node = cell.select_one("span#odds")
            odds = (
                odds_node.get_text(" ", strip=True)
                if odds_node
                else " ".join(cell.get_text(" ", strip=True).split())
            )

            row = {
                "組み合わせ": "-".join(combo_numbers),
                "オッズ": self._normalize_odds_value(odds),
            }
            if include_key:
                row["_cart_item"] = cart_item
            rows.append(row)

        dedup: dict[str, dict[str, str]] = {}
        for row in rows:
            key = row.get("_cart_item") or row["組み合わせ"]
            dedup[key] = row

        merged = list(dedup.values())
        merged.sort(key=lambda row: self._combo_sort_key(row.get("組み合わせ", "")))
        return merged

    @staticmethod
    def _combo_sort_key(combo: str) -> tuple[int, ...]:
        values = [int(x) for x in combo.split("-") if x.isdigit()]
        return tuple(values)

    @staticmethod
    def _normalize_odds_value(value: str) -> str:
        return value.replace(",", "").strip()

    def _fetch_html(self, url: str) -> str:
        response = self.session.get(url, timeout=self.options.timeout)
        response.raise_for_status()
        if not response.encoding:
            response.encoding = response.apparent_encoding
        return response.text

    @staticmethod
    def _extract_race_id(url: str) -> str | None:
        parsed = urlparse(url)
        race_id = parse_qs(parsed.query).get("race_id", [None])[0]
        return race_id

    @staticmethod
    def _extract_race_name(soup: BeautifulSoup) -> str | None:
        candidates = [
            soup.select_one("h1"),
            soup.select_one(".RaceName"),
            soup.select_one(".RaceData01"),
            soup.select_one("title"),
        ]
        for item in candidates:
            if item and item.get_text(strip=True):
                return item.get_text(" ", strip=True)
        return None

    def _extract_race_entries(self, soup: BeautifulSoup) -> list[dict[str, str]]:
        table = self._find_table_by_keywords(soup, ["馬名"]) or self._find_largest_table(soup)
        if table is None:
            return []
        return self._parse_table(table)

    def _extract_odds_from_page(self, soup: BeautifulSoup) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for table in soup.select("table"):
            label = self._resolve_table_label(table)
            if not label:
                continue
            matched = next((bet for bet in BET_TYPES if bet in label), None)
            if not matched:
                continue
            rows = self._parse_table(table)
            if rows:
                result[matched] = rows
        return result

    def _extract_win_place_by_table_position(self, soup: BeautifulSoup) -> dict[str, list[dict[str, str]]]:
        tables = soup.select("table")
        if len(tables) < 2:
            return {"単勝": [], "複勝": []}

        def parse_table(table_tag: Any) -> list[dict[str, str]]:
            rows: list[dict[str, str]] = []
            for tr in table_tag.select("tr"):
                cells = [td.get_text(" ", strip=True) for td in tr.select("td")]
                if len(cells) < 3:
                    continue
                horse_no = cells[1].strip() if len(cells) > 1 else ""
                if not horse_no.isdigit():
                    horse_no = next((value for value in cells if value.strip().isdigit()), "")
                odds = self._normalize_odds_value(cells[-1])
                horse_name = cells[-2].strip() if len(cells) >= 2 else ""
                if not horse_no:
                    continue
                rows.append(
                    {
                        "馬番": horse_no,
                        "馬名": horse_name,
                        "オッズ": odds,
                    }
                )
            return rows

        return {
            "単勝": parse_table(tables[0]),
            "複勝": parse_table(tables[1]),
        }

    def _extract_odds_by_bet_type(self, soup: BeautifulSoup) -> dict[str, list[dict[str, str]]]:
        result: dict[str, list[dict[str, str]]] = {bet_type: [] for bet_type in BET_TYPES}

        for heading in soup.select("h1, h2, h3, h4"):
            title = heading.get_text(" ", strip=True)
            normalized_title = title.replace("３", "3")

            table = heading.find_next("table")
            if not table:
                continue
            rows = self._parse_table(table)
            if not rows:
                continue

            if "単勝" in normalized_title and "複勝" in normalized_title:
                tansho_rows, fukusho_rows = self._split_tansho_fukusho(rows)
                if tansho_rows:
                    result["単勝"] = tansho_rows
                if fukusho_rows:
                    result["複勝"] = fukusho_rows
                continue

            if "馬連" in normalized_title and "ワイド" in normalized_title:
                umaren_rows, wide_rows = self._split_umaren_wide(rows)
                if umaren_rows:
                    result["馬連"] = umaren_rows
                if wide_rows:
                    result["ワイド"] = wide_rows
                continue

            if "枠連" in normalized_title:
                result["枠連"] = rows
            elif "馬単" in normalized_title:
                result["馬単"] = rows
            elif "3連複" in normalized_title:
                result["三連複"] = rows
            elif "3連単" in normalized_title:
                result["三連単"] = rows

        return result

    @staticmethod
    def _split_tansho_fukusho(rows: list[dict[str, str]]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
        tansho_rows: list[dict[str, str]] = []
        fukusho_rows: list[dict[str, str]] = []

        for row in rows:
            normalized = {key.replace(" ", ""): value for key, value in row.items()}
            base = {
                "人気": normalized.get("人気", ""),
                "ゲート": normalized.get("ゲート", ""),
                "馬番": normalized.get("馬番", ""),
                "馬名": normalized.get("馬名", ""),
            }

            tansho_value = normalized.get("単勝オッズ", normalized.get("単勝", ""))
            fukusho_value = normalized.get("複勝オッズ", normalized.get("複勝", ""))

            tansho_rows.append({**base, "オッズ": NetkeibaScraper._normalize_odds_value(tansho_value)})
            fukusho_rows.append({**base, "オッズ": NetkeibaScraper._normalize_odds_value(fukusho_value)})

        return tansho_rows, fukusho_rows

    @staticmethod
    def _split_umaren_wide(rows: list[dict[str, str]]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
        umaren_rows: list[dict[str, str]] = []
        wide_rows: list[dict[str, str]] = []

        for row in rows:
            normalized = {key.replace(" ", ""): value for key, value in row.items()}
            pair = normalized.get("組み合わせ", normalized.get("組合せ", ""))
            popularity = normalized.get("人気", "")
            umaren_odds = normalized.get("オッズ", "")
            wide_odds = normalized.get("ワイド・オッズ", normalized.get("ワイド", ""))

            umaren_rows.append(
                {
                    "人気": popularity,
                    "組み合わせ": pair,
                    "オッズ": NetkeibaScraper._normalize_odds_value(umaren_odds),
                }
            )
            wide_rows.append(
                {
                    "人気": popularity,
                    "組み合わせ": pair,
                    "オッズ": NetkeibaScraper._normalize_odds_value(wide_odds),
                }
            )

        return umaren_rows, wide_rows

    def _discover_odds_links(self, soup: BeautifulSoup, base_url: str) -> dict[str, str]:
        links: dict[str, str] = {}
        for anchor in soup.select("a[href]"):
            text = anchor.get_text(" ", strip=True)
            href = anchor.get("href", "")
            if not text and not href:
                continue
            for bet_type in BET_TYPES:
                if bet_type in text or bet_type in href:
                    links[bet_type] = urljoin(base_url, href)
        return links

    def _extract_best_table(self, soup: BeautifulSoup) -> list[dict[str, str]]:
        odds_table = self._find_table_by_keywords(soup, ["オッズ"]) or self._find_largest_table(soup)
        if odds_table is None:
            return []
        return self._parse_table(odds_table)

    @staticmethod
    def _resolve_table_label(table_tag: Any) -> str:
        caption = table_tag.find("caption")
        if caption:
            value = caption.get_text(" ", strip=True)
            if value:
                return value
        for tag in ["h1", "h2", "h3", "h4", "dt", "p", "span", "div"]:
            prev = table_tag.find_previous(tag)
            if prev and prev.get_text(strip=True):
                return prev.get_text(" ", strip=True)
        return ""

    @staticmethod
    def _find_largest_table(soup: BeautifulSoup):
        tables = soup.select("table")
        if not tables:
            return None
        return max(tables, key=lambda t: len(t.select("tr")))

    @staticmethod
    def _find_table_by_keywords(soup: BeautifulSoup, keywords: list[str]):
        for table in soup.select("table"):
            text = table.get_text(" ", strip=True)
            if all(keyword in text for keyword in keywords):
                return table
        return None

    @staticmethod
    def _parse_table(table_tag: Any) -> list[dict[str, str]]:
        rows = table_tag.select("tr")
        if not rows:
            return []

        headers = [th.get_text(" ", strip=True) for th in rows[0].select("th")]
        if not headers:
            thead = table_tag.select_one("thead tr")
            if thead:
                headers = [th.get_text(" ", strip=True) for th in thead.select("th")]

        data: list[dict[str, str]] = []
        for row in rows[1:]:
            cells = row.select("td")
            if not cells:
                continue
            values = [cell.get_text(" ", strip=True) for cell in cells]
            if headers and len(headers) == len(values):
                data.append(dict(zip(headers, values, strict=False)))
            else:
                data.append({f"col_{idx + 1}": value for idx, value in enumerate(values)})
        return data
