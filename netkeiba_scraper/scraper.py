from __future__ import annotations

from dataclasses import dataclass
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
        race_name = self._extract_race_name(soup)
        entries = self._extract_race_entries(soup)
        odds_links = self._discover_odds_links(soup, race_url)
        all_urls = dict(odds_links)

        odds: dict[str, Any] = {bet_type: [] for bet_type in BET_TYPES}

        for bet_type in BET_TYPES:
            try:
                rows, urls = self._collect_full_odds_for_bet_type(race_id, bet_type, entries)
                odds[bet_type] = rows
                for key, value in urls.items():
                    all_urls.setdefault(key, value)
            except Exception as exc:  # noqa: BLE001
                failed_url = self._build_abroad_type_url(race_id, bet_type)
                odds[bet_type] = [{"error": str(exc), "source_url": failed_url or ""}]

        return {
            "race_url": race_url,
            "race_id": race_id,
            "race_name": race_name,
            "entries": entries,
            "odds": odds,
            "odds_links": all_urls,
        }

    def _collect_full_odds_for_bet_type(
        self,
        race_id: str | None,
        bet_type: str,
        entries: list[dict[str, str]],
    ) -> tuple[list[dict[str, str]], dict[str, str]]:
        urls: dict[str, str] = {}

        if bet_type in {"単勝", "複勝"}:
            type_url = self._build_odds_type_url(race_id, bet_type)
            if not type_url:
                return [], urls
            urls[bet_type] = type_url
            html = self._fetch_html(type_url)
            soup = BeautifulSoup(html, "lxml")
            extracted = self._extract_odds_by_bet_type(soup)
            return extracted.get(bet_type, []), urls

        abroad_url = self._build_abroad_type_url(race_id, bet_type)
        if not abroad_url:
            return [], urls
        urls[bet_type] = abroad_url

        if bet_type in {"三連複", "三連単"}:
            rows, jiku_urls = self._collect_triple_full_odds(abroad_url, bet_type, entries)
            urls.update(jiku_urls)
            return rows, urls

        html = self._fetch_html(abroad_url)
        soup = BeautifulSoup(html, "lxml")
        rows = self._extract_cart_items(soup, bet_type)
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
            for key in ["馬番", "col_2", "col_1"]:
                value = row.get(key, "").strip()
                if value.isdigit() and value not in values:
                    values.append(value)
                    break
        return values

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
