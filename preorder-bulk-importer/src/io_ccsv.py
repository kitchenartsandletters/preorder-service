from __future__ import annotations

import re
from pathlib import Path
from typing import List, Dict, Optional

import pandas as pd

from .models import CandidateRow


def _clean_isbn(s: str) -> str:
    # keep digits and X; ISBN13 should be digits only, but we sanitize gently
    s = (s or "").strip()
    s = re.sub(r"[^0-9Xx]", "", s)
    return s.upper()


def read_candidates_from_csv(path: Path) -> List[CandidateRow]:
    df = pd.read_csv(path, dtype=str).fillna("")
    # Heuristic: find an ISBN column. Prefer exact matches first.
    cols = {c.lower().strip(): c for c in df.columns}
    isbn_col = None
    for key in ["isbn13", "isbn", "barcode"]:
        if key in cols:
            isbn_col = cols[key]
            break

    if isbn_col is None:
        # fallback: first column containing 'isbn'
        for c in df.columns:
            if "isbn" in c.lower():
                isbn_col = c
                break

    if isbn_col is None:
        raise RuntimeError(f"Could not find ISBN column in {path.name}. Columns: {list(df.columns)}")

    # Optional vendor/designation columns
    vendor_col = next((c for c in df.columns if c.lower().strip() in ["vendor", "publisher", "imprint"]), None)
    designation_col = next((c for c in df.columns if "designation" in c.lower() or "capsule" in c.lower() or "season" in c.lower()), None)

    out: List[CandidateRow] = []
    for _, row in df.iterrows():
        isbn = _clean_isbn(str(row.get(isbn_col, "")))
        if not isbn:
            continue
        vendor = str(row.get(vendor_col, "")).strip() if vendor_col else None
        designation = str(row.get(designation_col, "")).strip() if designation_col else None
        raw: Dict[str, str] = {k: str(v) for k, v in row.items()}
        out.append(CandidateRow(isbn13=isbn, vendor=vendor or None, designation=designation or None, raw=raw))
    return out


def read_isbns_from_file(path: Path) -> List[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    isbns = []
    for ln in lines:
        isbn = _clean_isbn(ln)
        if isbn:
            isbns.append(isbn)
    return isbns


def write_output_csv_using_template(
    template_csv: Path,
    output_csv: Path,
    rows: List[Dict[str, str]],
) -> None:
    """
    Write a CSV whose columns match the template CSV's columns exactly (order + names).
    Any missing keys in `rows` become empty strings.
    Extra keys in `rows` are ignored.
    """
    template_df = pd.read_csv(template_csv, dtype=str).fillna("")
    columns = list(template_df.columns)

    out_df = pd.DataFrame([{c: (r.get(c, "") or "") for c in columns} for r in rows], columns=columns)
    out_df.to_csv(output_csv, index=False)