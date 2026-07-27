#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from pypdf import PdfReader

HEADER = ["月", "日", "種別", "利用駅", "種別", "利用駅", "残高", "入金・利用額"]

ISSUE_DATE_PATTERN = re.compile(r"(20[0-9]{2})/([0-9]{1,2})/([0-9]{1,2})")
# Examples:
# -14612 入 御徒町 出 秋葉原 \1,99826
# +3,00012 VIEW モバイル \4,09826
# -90012 物販 \1,09826
ROW_PATTERN = re.compile(r"^([+-][0-9,]+)([0-9]{2})\s+(.+?)\s+\\([0-9,]+)([0-9]{2})$")
# Example (carry-over): 12 繰 \2,14420
CARRY_PATTERN = re.compile(r"^([0-9]{2})\s+(.+?)\s+\\([0-9,]+)([0-9]{2})$")


@dataclass(frozen=True)
class UsageRow:
    month: str
    day: str
    type1: str
    station1: str
    type2: str
    station2: str
    balance: str
    amount: str
    sort_date: date | None

    def as_csv_row(self) -> list[str]:
        return [
            self.month,
            self.day,
            self.type1,
            self.station1,
            self.type2,
            self.station2,
            self.balance,
            self.amount,
        ]

    def dedupe_key(self) -> tuple[str, ...]:
        return tuple(self.as_csv_row())


def normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def normalize_station(text: str) -> str:
    return re.sub(r"\s+", "", text.strip())


def parse_middle_segment(segment: str) -> tuple[str, str, str, str]:
    segment = normalize_spaces(segment)
    tokens = segment.split(" ") if segment else []

    if not tokens:
        return "", "", "", ""

    if len(tokens) >= 4 and tokens[0] == "入" and "出" in tokens[1:]:
        out_index = tokens.index("出", 1)
        station1 = normalize_station("".join(tokens[1:out_index]))
        station2 = normalize_station("".join(tokens[out_index + 1 :]))
        return "入", station1, "出", station2

    if len(tokens) == 1:
        return tokens[0], "", "", ""

    if len(tokens) >= 2:
        return tokens[0], normalize_station(tokens[1]), "", ""

    return tokens[0], "", "", ""


def extract_issue_date(text: str) -> date | None:
    matches = list(ISSUE_DATE_PATTERN.finditer(text))
    if not matches:
        return None

    y, m, d = matches[-1].groups()
    try:
        return date(int(y), int(m), int(d))
    except ValueError:
        return None


def infer_year(issue: date | None, month: int) -> int | None:
    if issue is None:
        return None
    # Suica残高履歴は通常、発行月以前の履歴を含む。
    # 発行月より大きい月は前年とみなす。
    return issue.year - 1 if month > issue.month else issue.year


def parse_usage_rows_from_text(text: str, source: Path) -> list[UsageRow]:
    issue = extract_issue_date(text)
    rows: list[UsageRow] = []

    for raw_line in text.splitlines():
        line = normalize_spaces(raw_line)
        if not line:
            continue

        m = ROW_PATTERN.match(line)
        if m:
            amount_raw, month_raw, middle, balance_raw, day_raw = m.groups()
            month = f"{int(month_raw):02d}"
            day = f"{int(day_raw):02d}"
            amount = amount_raw.replace(",", "")
            balance = balance_raw.replace(",", "")
            type1, station1, type2, station2 = parse_middle_segment(middle)

            year = infer_year(issue, int(month))
            sort_value = date(year, int(month), int(day)) if year is not None else None
            rows.append(
                UsageRow(month, day, type1, station1, type2, station2, balance, amount, sort_value)
            )
            continue

        c = CARRY_PATTERN.match(line)
        if c:
            month_raw, kind_raw, balance_raw, day_raw = c.groups()
            month = f"{int(month_raw):02d}"
            day = f"{int(day_raw):02d}"
            balance = balance_raw.replace(",", "")
            kind = normalize_spaces(kind_raw)

            year = infer_year(issue, int(month))
            sort_value = date(year, int(month), int(day)) if year is not None else None
            rows.append(UsageRow(month, day, kind, "", "", "", balance, "", sort_value))

    if not rows:
        print(f"Warn: no usage rows parsed from {source.name}", file=sys.stderr)

    return rows


def parse_pdf(pdf_path: Path) -> list[UsageRow]:
    try:
        reader = PdfReader(str(pdf_path))
    except Exception as exc:
        print(f"Warn: failed to open {pdf_path.name}: {exc}", file=sys.stderr)
        return []

    text_parts: list[str] = []
    for page in reader.pages:
        try:
            text_parts.append(page.extract_text() or "")
        except Exception:
            continue

    all_text = "\n".join(text_parts)
    return parse_usage_rows_from_text(all_text, pdf_path)


def load_existing_csv(csv_path: Path) -> list[UsageRow]:
    if not csv_path.exists():
        return []

    rows: list[UsageRow] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for item in reader:
            if item is None:
                continue
            month = (item.get("月") or "").strip()
            day = (item.get("日") or "").strip()
            if not month or not day:
                continue
            rows.append(
                UsageRow(
                    month=f"{int(month):02d}",
                    day=f"{int(day):02d}",
                    type1=(item.get("種別") or "").strip(),
                    station1=(item.get("利用駅") or "").strip(),
                    type2=(item.get("種別.1") or item.get("種別_2") or "").strip(),
                    station2=(item.get("利用駅.1") or item.get("利用駅_2") or "").strip(),
                    balance=(item.get("残高") or "").strip(),
                    amount=(item.get("入金・利用額") or "").strip(),
                    sort_date=None,
                )
            )
    return rows


def sort_key(row: UsageRow) -> tuple[int, int, int, str, str, str, str, str, str]:
    if row.sort_date is not None:
        return (
            row.sort_date.year,
            row.sort_date.month,
            row.sort_date.day,
            row.type1,
            row.station1,
            row.type2,
            row.station2,
            row.balance,
            row.amount,
        )

    return (
        9999,
        int(row.month),
        int(row.day),
        row.type1,
        row.station1,
        row.type2,
        row.station2,
        row.balance,
        row.amount,
    )


def merge_dedupe_sort(existing: list[UsageRow], new_rows: list[UsageRow]) -> list[UsageRow]:
    merged: list[UsageRow] = []
    seen: dict[tuple[str, ...], UsageRow] = {}

    # Existing rows first, then overwrite with new rows when same key appears
    # so newly parsed rows can contribute sort_date.
    for row in existing:
        key = row.dedupe_key()
        if key not in seen:
            seen[key] = row

    for row in new_rows:
        seen[row.dedupe_key()] = row

    merged.extend(seen.values())
    merged.sort(key=sort_key)
    return merged


def write_csv(csv_path: Path, rows: list[UsageRow]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(HEADER)
        for row in rows:
            writer.writerow(row.as_csv_row())


def extract_id_prefix(pdf_name: str) -> str | None:
    m = re.match(r"^(JE[^_]+)_", pdf_name)
    if not m:
        return None
    return m.group(1)


def output_csv_path_for_id(output_dir: Path, csv_template: str, id_prefix: str) -> Path:
    filename = csv_template.format(id=id_prefix)
    return output_dir / filename


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract JE usage details from PDF and merge into CSV.")
    parser.add_argument(
        "--pdf-glob",
        default="JE*.pdf",
        help="glob pattern for target PDFs (default: JE*.pdf)",
    )
    parser.add_argument(
        "--output-dir",
        default=".",
        help="output directory for CSV files (default: current directory)",
    )
    parser.add_argument(
        "--csv-template",
        default="{id}_usage.csv",
        help="output CSV filename template. Use {id} as JE ID placeholder (default: {id}_usage.csv)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="parse and merge but do not write CSV",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="ignore existing CSV and rebuild only from current PDF files",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)

    pdf_files = sorted(Path(".").glob(args.pdf_glob))
    if not pdf_files:
        print(f"No PDF files found: {args.pdf_glob}")
        return 1

    grouped: dict[str, list[Path]] = {}
    skipped = 0
    for pdf in pdf_files:
        id_prefix = extract_id_prefix(pdf.name)
        if not id_prefix:
            print(f"Skip: ID prefix not found in filename: {pdf.name}", file=sys.stderr)
            skipped += 1
            continue
        grouped.setdefault(id_prefix, []).append(pdf)

    if not grouped:
        print("No JE ID groups found from filenames.")
        return 1

    total_written = 0
    total_groups = 0

    for id_prefix, files in sorted(grouped.items()):
        total_groups += 1
        csv_path = output_csv_path_for_id(output_dir, args.csv_template, id_prefix)
        new_rows: list[UsageRow] = []

        for pdf in files:
            parsed = parse_pdf(pdf)
            new_rows.extend(parsed)
            print(f"[{id_prefix}] Parsed {pdf.name}: {len(parsed)} rows")

        existing_rows = [] if args.rebuild else load_existing_csv(csv_path)
        merged_rows = merge_dedupe_sort(existing_rows, new_rows)

        print(
            f"[{id_prefix}] Summary: files={len(files)}, existing={len(existing_rows)}, "
            f"new={len(new_rows)}, merged={len(merged_rows)}, "
            f"deduped={len(existing_rows) + len(new_rows) - len(merged_rows)}"
        )

        if args.dry_run:
            print(f"[{id_prefix}] Dry-run mode: CSV was not written: {csv_path}")
            continue

        write_csv(csv_path, merged_rows)
        total_written += 1
        print(f"[{id_prefix}] Wrote CSV: {csv_path}")

    print(f"Done: groups={total_groups}, csv_written={total_written}, files_skipped={skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
