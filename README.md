# netkeiba オッズ / 出馬表スクレイパー

netkeiba のレースページ URL から、出馬表とオッズを取得し、JSON と券種別 CSV に出力する CLI ツールです。

## 主な機能

- レースURLから `race_id` / レース名 / 出馬表を取得
- 対応券種: 単勝 / 複勝 / 枠連 / 馬連 / ワイド / 馬単 / 三連複 / 三連単
- `馬連 / ワイド / 馬単` は全組み合わせを取得
- `三連複 / 三連単` は `jiku` を全馬番で走査し、全組み合わせを収集
- オッズの桁区切り `,` を除去して出力（例: `1,042.6` → `1042.6`）
- 単発実行と URL リスト一括処理をサポート

> 注意: サイト構造変更・アクセス制限・Bot対策の影響で、取得結果が変わる可能性があります。

## 動作環境

- Python 3.10+
- Linux / macOS / Windows (WSL含む)

## セットアップ

```bash
pip install -r requirements.txt
```

## 使い方

### 1) 単発実行（JSONのみ）

```bash
python -m netkeiba_scraper \
  --url "https://race.netkeiba.com/race/shutuba_abroad.html?race_id=2026P0010109&rf=race_submenu" \
  --output out/saudi_cup.json
```

### 2) 単発実行（JSON + 券種別CSV）

```bash
python -m netkeiba_scraper \
  --url "https://race.netkeiba.com/race/shutuba_abroad.html?race_id=2026P0010109&rf=race_submenu" \
  --output out/saudi_cup.json \
  --csv-dir out/saudi_cup_csv
```

### 3) 一括実行（URLリスト）

`out/urls.txt`（1行1URL、`#` 始まりはコメント）を用意:

```txt
# saudi cup
https://race.netkeiba.com/race/shutuba_abroad.html?race_id=2026P0010109&rf=race_submenu
https://race.netkeiba.com/race/shutuba_abroad.html?race_id=2026P0010108&rf=race_submenu
```

実行:

```bash
python -m netkeiba_scraper \
  --url-file out/urls.txt \
  --batch-output-dir out/batch
```

出力先（例）:

- `out/batch/2026P0010109/race_data.json`
- `out/batch/2026P0010109/csv/三連単.csv`
- 同じ `race_id` が複数行ある場合は `2026P0010109_02` のように連番サフィックスで保存

## CLIオプション

- `--url`: 単一レースURL
- `--url-file`: レースURLリストファイル（`--url` と排他）
- `--output`: 単発実行時のJSON出力先
- `--csv-dir`: 単発実行時のCSV出力先
- `--batch-output-dir`: 一括実行時のルート出力ディレクトリ
- `--indent`: JSONインデント

## 出力フォーマット

JSON:

```json
{
  "race_url": "...",
  "race_id": "2026P0010109",
  "race_name": "サウジカップ",
  "entries": [
    {"col_2": "3", "col_4": "フォーエバーヤング"}
  ],
  "odds": {
    "三連単": [
      {"組み合わせ": "1-2-3", "オッズ": "1914.6"}
    ]
  },
  "odds_links": {
    "三連単": "https://race.netkeiba.com/odds/abroad.html?type=b8&race_id=..."
  }
}
```

CSV:

- 各券種ごとに1ファイル（例: `三連単.csv`）
- 主に `組み合わせ,オッズ` 列
- `オッズ` 列はカンマ除去済み

## 実装概要

- `netkeiba_scraper/scraper.py`
  - 出馬表取得
  - 券種別オッズ取得
  - 三連複/三連単の `jiku` 走査と重複排除
- `netkeiba_scraper/csv_exporter.py`
  - 券種別CSV書き出し
- `netkeiba_scraper/cli.py`
  - 単発 / 一括モード制御

## トラブルシュート

- **取得件数が急に減った**: サイト側HTML構造変更の可能性。`out/*.json` の `odds_links` を確認し、対象ページの DOM を再確認してください。
- **一部券種が空**: レースによって該当券種が提供されない場合があります（例: 枠連 0件）。
- **アクセス失敗**: 通信制限・タイムアウト・Bot対策の影響が考えられます。時間を空けて再試行してください。

## GitHub公開手順

```bash
git init
git add .
git commit -m "Add netkeiba odds scraper with batch CSV export"
git branch -M main
git remote add origin <YOUR_GITHUB_REPO_URL>
git push -u origin main
```

## 免責

取得したデータは参考情報です。最終的な数値は必ず主催者発表の情報で確認してください。
