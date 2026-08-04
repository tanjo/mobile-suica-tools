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

HEADER = ["年", "月", "日", "種別", "利用駅", "種別", "利用駅", "残高", "入金・利用額"]

DATE_PATTERN = re.compile(r"(20[0-9]{2})/([0-9]{1,2})/([0-9]{1,2})")
# Examples:
# -14612 入 御徒町 出 秋葉原 \1,99826
# +3,00012 VIEW モバイル \4,09826
# -90012 物販 \1,09826
ROW_PATTERN = re.compile(r"^([+-][0-9,]+)([0-9]{2})\s+(.+?)\s+\\([0-9,]+)([0-9]{2})$")
# Example (carry-over): 12 繰 \2,14420
CARRY_PATTERN = re.compile(r"^([0-9]{2})\s+(.+?)\s+\\([0-9,]+)([0-9]{2})$")


@dataclass(frozen=True)
class UsageRow:
    year: str
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
            self.year,
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
        # 年・月・日・種別・駅・残高・入金・利用額をすべて含めて同じ行を判定する。
        # これにより同日同ルートの別取引を別行として保持できる。
        return (
            self.year,
            self.month,
            self.day,
            self.type1,
            self.station1,
            self.type2,
            self.station2,
            self.balance,
            self.amount,
        )

    def _int_value(self, value: str) -> int | None:
        if not value:
            return None
        try:
            return int(value.replace(",", ""))
        except ValueError:
            return None

    def balance_value(self) -> int | None:
        return self._int_value(self.balance)

    def amount_value(self) -> int | None:
        return self._int_value(self.amount)

    def prev_balance_value(self) -> int | None:
        balance = self.balance_value()
        amount = self.amount_value()
        if balance is None or amount is None:
            return None
        return balance - amount


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


def _build_date(y: str, m: str, d: str) -> date | None:
    try:
        return date(int(y), int(m), int(d))
    except ValueError:
        return None


def extract_statement_date(text: str) -> date | None:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    # 優先: 明細ヘッダ付近の案内文を含むブロックの日付
    # 例: 2026/6/21 の直後に「ご利用ありがとうございます。」が続く
    for i, line in enumerate(lines):
        m = DATE_PATTERN.search(line)
        if not m:
            continue

        window = " ".join(lines[i + 1 : i + 4])
        if "ご利用ありがとうございます" in window or "最新のご利用明細" in window:
            y, mo, d = m.groups()
            dt = _build_date(y, mo, d)
            if dt is not None:
                return dt

    # 次点: 発行日時ラベル近傍の日付
    for i, line in enumerate(lines):
        if "発行日時" not in line:
            continue
        for candidate in lines[max(0, i - 1) : i + 3]:
            m = DATE_PATTERN.search(candidate)
            if not m:
                continue
            y, mo, d = m.groups()
            dt = _build_date(y, mo, d)
            if dt is not None:
                return dt

    # 最終フォールバック: 文書内の最後の日付
    matches = list(DATE_PATTERN.finditer(text))
    if not matches:
        return None

    y, m, d = matches[-1].groups()
    return _build_date(y, m, d)


def apply_years_by_row_order(rows: list[UsageRow], statement_date: date | None) -> list[UsageRow]:
    if not rows:
        return rows

    if statement_date is None:
        return rows

    def assign_from_newest_to_oldest(src_rows: list[UsageRow]) -> list[UsageRow]:
        current_year = statement_date.year
        first_md = (int(src_rows[0].month), int(src_rows[0].day))
        statement_md = (statement_date.month, statement_date.day)
        if first_md > statement_md:
            current_year -= 1

        rewritten: list[UsageRow] = []
        prev_md: tuple[int, int] | None = None

        for row in src_rows:
            current_md = (int(row.month), int(row.day))
            if prev_md is not None and current_md > prev_md:
                # 新しい順で並ぶ前提: 月日が増えたら前年へロールオーバー。
                current_year -= 1

            rewritten.append(
                UsageRow(
                    year=f"{current_year:04d}",
                    month=row.month,
                    day=row.day,
                    type1=row.type1,
                    station1=row.station1,
                    type2=row.type2,
                    station2=row.station2,
                    balance=row.balance,
                    amount=row.amount,
                    sort_date=date(current_year, int(row.month), int(row.day)),
                )
            )
            prev_md = current_md

        return rewritten

    inc = 0
    dec = 0
    for i in range(1, len(rows)):
        prev_md = (int(rows[i - 1].month), int(rows[i - 1].day))
        cur_md = (int(rows[i].month), int(rows[i].day))
        if cur_md > prev_md:
            inc += 1
        elif cur_md < prev_md:
            dec += 1

    # 増加が多い場合は古い順とみなし、反転して同じロジックを適用する。
    if inc > dec:
        reversed_assigned = assign_from_newest_to_oldest(list(reversed(rows)))
        return list(reversed(reversed_assigned))

    return assign_from_newest_to_oldest(rows)


def parse_usage_rows_from_text(text: str, source: Path) -> list[UsageRow]:
    statement_date = extract_statement_date(text)
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

            rows.append(
                UsageRow("", month, day, type1, station1, type2, station2, balance, amount, None)
            )
            continue

        c = CARRY_PATTERN.match(line)
        if c:
            month_raw, kind_raw, balance_raw, day_raw = c.groups()
            month = f"{int(month_raw):02d}"
            day = f"{int(day_raw):02d}"
            balance = balance_raw.replace(",", "")
            kind = normalize_spaces(kind_raw)

            rows.append(UsageRow("", month, day, kind, "", "", "", balance, "", None))

    if not rows:
        print(f"Warn: no usage rows parsed from {source.name}", file=sys.stderr)
        return rows

    rows = apply_years_by_row_order(rows, statement_date)

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
        reader = csv.reader(f)
        headers = next(reader, None)
        if headers is None:
            return []

        for row in reader:
            if not row:
                continue
            row = [cell.strip() for cell in row]
            if len(row) < 9:
                row += [""] * (9 - len(row))

            year, month, day, type1, station1, type2, station2, balance, amount = row[:9]
            if not month or not day:
                continue

            rows.append(
                UsageRow(
                    year=year,
                    month=f"{int(month):02d}",
                    day=f"{int(day):02d}",
                    type1=type1,
                    station1=station1,
                    type2=type2,
                    station2=station2,
                    balance=balance,
                    amount=amount,
                    sort_date=None,
                )
            )
    return rows


def sort_key(row: UsageRow) -> tuple[int, int, int, int, int, int, str, str, str, str, str]:
    if row.sort_date is not None:
        return (
            row.sort_date.year,
            row.sort_date.month,
            row.sort_date.day,
            0,
            0,
            0,
            row.type1,
            row.station1,
            row.type2,
            row.station2,
            row.amount,
        )

    if row.year:
        return (
            int(row.year),
            int(row.month),
            int(row.day),
            0,
            0,
            0,
            row.type1,
            row.station1,
            row.type2,
            row.station2,
            row.amount,
        )

    return (
        9999,
        int(row.month),
        int(row.day),
        0,
        0,
        0,
        row.type1,
        row.station1,
        row.type2,
        row.station2,
        row.amount,
    )


def reorder_same_date_group(rows: list[UsageRow], prev_balance: int | None) -> list[UsageRow]:
    if len(rows) <= 1:
        return rows

    carry_rows = [r for r in rows if r.type1 == "繰"]
    normal_rows = [r for r in rows if r.type1 != "繰"]

    def build_chain(src_rows: list[UsageRow], start_balance: int | None) -> list[UsageRow]:
        chain: list[UsageRow] = []
        remaining = set(src_rows)
        current_balance = start_balance

        while remaining:
            candidates = [r for r in remaining if r.prev_balance_value() == current_balance]
            if not candidates:
                break
            next_row = min(
                candidates,
                key=lambda r: (
                    r.balance_value() or 0,
                    r.amount_value() or 0,
                    r.type1,
                    r.station1,
                    r.type2,
                    r.station2,
                ),
            )
            chain.append(next_row)
            remaining.remove(next_row)
            current_balance = next_row.balance_value()

        if len(chain) == len(src_rows):
            return chain

        return sorted(
            src_rows,
            key=lambda r: (
                r.prev_balance_value() or 0,
                r.balance_value() or 0,
                r.amount_value() or 0,
                r.type1,
                r.station1,
                r.type2,
                r.station2,
            ),
        )

    ordered = build_chain(normal_rows, prev_balance)

    for carry in carry_rows:
        inserted = False
        carry_balance = carry.balance_value()
        if carry_balance is not None:
            for index, existing in enumerate(ordered):
                if existing.balance_value() == carry_balance:
                    ordered.insert(index + 1, carry)
                    inserted = True
                    break
        if not inserted:
            ordered.append(carry)

    return ordered


def sort_rows(rows: list[UsageRow]) -> list[UsageRow]:
    rows = sorted(rows, key=sort_key)
    grouped: list[UsageRow] = []
    i = 0
    while i < len(rows):
        group = [rows[i]]
        j = i + 1
        while j < len(rows) and rows[j].year == rows[i].year and rows[j].month == rows[i].month and rows[j].day == rows[i].day:
            group.append(rows[j])
            j += 1

        prev_balance = grouped[-1].balance_value() if grouped and grouped[-1].balance_value() is not None else None
        grouped.extend(reorder_same_date_group(group, prev_balance))
        i = j

    return grouped


def merge_dedupe_sort(existing: list[UsageRow], new_rows: list[UsageRow]) -> list[UsageRow]:
    merged: list[UsageRow] = []
    seen: dict[tuple[str, ...], UsageRow] = {}

    # 既存データを保持し、重複がない新規行のみ追加する。
    for row in existing:
        seen[row.dedupe_key()] = row

    for row in new_rows:
        key = row.dedupe_key()
        if key not in seen:
            seen[key] = row

    merged.extend(seen.values())
    return sort_rows(merged)


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
