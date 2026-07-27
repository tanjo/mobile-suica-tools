#!/usr/bin/env python3

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from pypdf import PdfReader


DATE_TOKEN_PATTERN = re.compile(r"[0-9０-９]{4}[./\-年][0-9０-９]{1,2}[./\-月][0-9０-９]{1,2}日?|[0-9０-９]{8}")

FULLWIDTH_TRANS = str.maketrans("０１２３４５６７８９", "0123456789")


def normalize_digits(text: str) -> str:
    return text.translate(FULLWIDTH_TRANS)


def to_yyyymmdd(raw: str) -> str | None:
    s = normalize_digits(raw)
    m = re.search(r"([0-9]{4})[年/\-.]([0-9]{1,2})[月/\-.]([0-9]{1,2})日?", s)
    if m:
        y, mo, d = m.groups()
        return f"{int(y):04d}{int(mo):02d}{int(d):02d}"

    m = re.search(r"\b([0-9]{8})\b", s)
    if m:
        return m.group(1)

    return None


def extract_purchase_date_from_pdf(pdf_path: Path) -> str | None:
    try:
        reader = PdfReader(str(pdf_path))
    except Exception:
        return None

    chunks: list[str] = []
    for page in reader.pages:
        try:
            chunks.append(page.extract_text() or "")
        except Exception:
            continue

    text = "\n".join(chunks)
    if not text.strip():
        return None

    normalized = normalize_digits(text)
    lines = [ln.strip() for ln in normalized.splitlines() if ln.strip()]

    # Suica明細専用: 「購入年月日」見出しの直後ブロックのみを探索する。
    for i, line in enumerate(lines):
        if line != "購入年月日":
            continue

        window = lines[i + 1 : i + 12]
        for candidate_line in window:
            # 発行日時を含む行は除外して誤抽出を防ぐ。
            if "発行日時" in candidate_line:
                continue

            m = DATE_TOKEN_PATTERN.search(candidate_line)
            if not m:
                continue

            date_str = to_yyyymmdd(m.group(0))
            if date_str:
                return date_str

    return None


def unique_destination(path: Path) -> Path:
    if not path.exists():
        return path

    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    n = 1
    while True:
        candidate = parent / f"{stem}_{n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1


def rename_files(dry_run: bool) -> int:
    files = sorted(Path(".").glob("meisai_*.pdf"))
    if not files:
        print("No meisai_*.pdf files found.")
        return 0

    renamed = 0
    skipped = 0
    failed = 0

    for file_path in files:
        name = file_path.name

        if re.match(r"^meisai_[0-9]{8}_.+\.pdf$", name):
            print(f"Skip (already renamed): {name}")
            skipped += 1
            continue

        purchase_date = extract_purchase_date_from_pdf(file_path)
        if not purchase_date:
            print(f"Skip (purchase date not found in 購入年月日 section): {name}")
            failed += 1
            continue

        suffix = name[len("meisai_") :]
        dest = Path(f"meisai_{purchase_date}_{suffix}")

        if dest != file_path:
            dest = unique_destination(dest)

        if dry_run:
            print(f"[DRY-RUN] mv -- \"{name}\" \"{dest.name}\"")
        else:
            file_path.rename(dest)
            print(f"Renamed: {name} -> {dest.name}")
        renamed += 1

    print(f"Done. renamed={renamed}, skipped={skipped}, not_found={failed}, dry_run={dry_run}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rename meisai PDFs by extracting purchase date from PDF body text."
    )
    parser.add_argument("--dry-run", action="store_true", help="show rename plan without changing files")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return rename_files(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
