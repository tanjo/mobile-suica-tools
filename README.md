## mobile-suica-tools

モバイルSuicaのPDF明細からCSVを作るためのスクリプト集です。

## 前提

- Python 3.11
- pipenv

依存関係のインストール:

```bash
pipenv install
```

## 1. JE明細PDFから利用履歴CSVを作成

対象スクリプト: extract_je_usage_csv.py

### 実行例

```bash
# PDFからCSVを再構築（既存CSVは無視）
pipenv run python extract_je_usage_csv.py --rebuild

# 既存CSVにマージ（重複除去あり）
pipenv run python extract_je_usage_csv.py

# 書き込みせず確認のみ
pipenv run python extract_je_usage_csv.py --dry-run
```

### 入力

- カレントディレクトリの JE*.pdf
- ファイル名先頭のID (例: JE80F...) ごとにグループ化

### 出力

- 既定ファイル名: {id}_usage.csv
- 既定列:
	- 年
	- 月
	- 日
	- 種別
	- 利用駅
	- 種別
	- 利用駅
	- 残高
	- 入金・利用額

### 年の決め方

1. PDF本文から基準日を抽出
	 - 優先: 日付行の直後に「ご利用ありがとうございます」または「最新のご利用明細」を含む箇所
	 - 次点: 「発行日時」近傍の日付
	 - 最後のフォールバック: 本文中の最後の日付
2. 取引行の並び順を自動判定（新しい順 / 古い順）
3. 年またぎで月日が逆転したタイミングで年を調整

### 並び順について

- 最終CSVは日付 (年, 月, 日) で整列
- 同日の複数取引は、PDFから読み取った出現順を保持

### 主なオプション

```bash
pipenv run python extract_je_usage_csv.py \
	--pdf-glob "JE*.pdf" \
	--output-dir . \
	--csv-template "{id}_usage.csv"
```

## 2. meisai_*.pdf のリネーム

対象スクリプト: rename_meisai_with_date.py

PDF本文の「購入年月日」セクションから日付を抽出し、
meisai_YYYYMMDD_...pdf 形式にリネームします。

### 実行例

```bash
# 変更内容の確認のみ
pipenv run python rename_meisai_with_date.py --dry-run

# 実際にリネーム
pipenv run python rename_meisai_with_date.py
```

## トラブルシュート

- 明細行が取れない場合:
	- PDFが画像ベースで文字抽出できない可能性があります
	- pypdfで本文テキストが取得できるPDFを使用してください
- 年が期待とずれる場合:
	- 基準日候補となる本文日付が想定どおりに入っているか確認してください
	- 年またぎ判定は「行順」と「月日の逆転」を使います

## おまけ
### .git/pre-push
CSV の履歴をコミットして残したい場合、.git/pre-push フックを利用して push することを防止できます。

例として、 `history` ブランチにCSVの履歴を保存している場合の設定例を示します。

以下の内容を `.git/pre-push` に記述してください。

```bash
#!/bin/sh

protected_branch="history"
current_branch=$(git symbolic-ref --short HEAD)

if [ "$current_branch" = "$protected_branch" ]; then
  echo "You are trying to push to the protected branch '$protected_branch'."
  echo "Please switch to a different branch before pushing."
  exit 1
fi

exit 0
```

これによって `history` ブランチへの push が制限され、CSV の履歴を誤って公の場で公開してしまうことを防止できます。
