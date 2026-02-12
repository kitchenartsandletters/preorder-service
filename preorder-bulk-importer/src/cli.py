from __future__ import annotations

import argparse
from pathlib import Path
from typing import List, Optional, Dict

from .config import get_paths, get_edelweiss_config, Defaults, PlaywrightConfig
from .io_csv import read_candidates_from_csv, read_isbns_from_file, write_output_csv_using_template
from .models import EnrichedRecord, ImageAsset
from .reporting.run_report import RunReport, RunContext, new_run_id
from .reporting.anomalies import validate_record
from .edelweiss.client import EdelweissClient
from .edelweiss.parser import extract_record_from_page
from .edelweiss.images import (
    download_images_for_record,
    extract_interior_images,
)
from .utils.text import (
    build_handle,
    clean_edelweiss_body_html,
    build_preorder_body,
)


def parse_isbns(args) -> List[str]:
    if args.isbns:
        return [s.strip() for s in args.isbns.split(",") if s.strip()]
    if args.isbn_file:
        return read_isbns_from_file(Path(args.isbn_file))
    if args.input:
        candidates = read_candidates_from_csv(Path(args.input))
        return [c.isbn13 for c in candidates]
    raise RuntimeError("No ISBN source provided. Use --input, --isbn-file, or --isbns.")    

def build_rows_for_shopify_csv(rec: EnrichedRecord, template_columns: List[str]) -> List[Dict[str, str]]:
    """
    Canonical mapping for preorder_products_export.csv.
    Only writes values for columns that exist in the template.
    All unspecified columns are intentionally left blank.
    """
    rows: List[Dict[str, str]] = []
    row: Dict[str, str] = {}

    def set_if(col: str, val) -> None:
        if col in template_columns:
            row[col] = "" if val is None else str(val)

    # --- Core product fields ---
    set_if("Handle", rec.handle)
    set_if("Title", rec.title)
    set_if("Body (HTML)", rec.body_html)
    set_if("Vendor", rec.vendor)
    set_if("Product Category", "Media > Books > Print Books")
    set_if("Type", "BOOK")
    set_if("Published", "FALSE")
    set_if("Status", "draft")

    # --- Tags ---
    if rec.tags:
        set_if("Tags", ", ".join(rec.tags))

    # --- Single variant definition ---
    set_if("Option1 Name", "Title")
    set_if("Option1 Value", "Default")

    set_if("Variant SKU", rec.authors_display)
    set_if("Variant Barcode", rec.isbn13)
    set_if("Variant Price", rec.price_usd)
    set_if("Variant Compare At Price", "")

    set_if("Variant Inventory Tracker", "shopify")
    set_if("Variant Inventory Policy", "continue")
    set_if("Variant Fulfillment Service", "manual")
    set_if("Variant Requires Shipping", "TRUE")
    set_if("Variant Taxable", "TRUE")
    set_if("Variant Tax Code", "4901.99")

    # 5 lb → grams (rounded)
    set_if("Variant Grams", 2268)
    set_if("Variant Weight Unit", "lb")

    cdn_prefix = "https://cdn.shopify.com/s/files/1/0297/5046/0549/files/"

    # Cover image (position 1)
    cover = next((a for a in rec.images if a.kind == "cover"), None)
    if cover:
        set_if("Image Src", f"{cdn_prefix}{rec.isbn13}.jpg")
        set_if("Image Position", 1)
        alt = rec.seo_title or rec.title or ""
        set_if("Image Alt Text", f"Book Cover: {alt}".strip())

    # --- SEO ---
    set_if("SEO Title", rec.seo_title)
    # SEO Description intentionally left blank (Gemini phase later)

    # --- Google Shopping (safe defaults) ---
    set_if("Google Shopping / Google Product Category", "Media > Books")
    set_if("Google Shopping / Condition", "new")
    set_if("Google Shopping / Custom Product", "FALSE")

    # --- Custom metafields ---
    set_if("Author (product.metafields.custom.author)", rec.authors_display)
    set_if(
        "Canonical Handle (product.metafields.custom.canonical_handle)",
        rec.canonical_handle,
    )
    set_if("Binding (product.metafields.custom.binding)", rec.binding_label)
    set_if("Language (product.metafields.custom.language)", "English")
    set_if("Publication Date (product.metafields.custom.pub_date)", rec.pub_date)

    # Append primary (cover) row
    rows.append(row)

    # Interior images (positions 2–6)
    interiors = [a for a in rec.images if a.kind == "interior"][:5]
    for idx, _asset in enumerate(interiors, start=2):
        row_i: Dict[str, str] = {}

        row_i["Handle"] = rec.handle
        row_i["Image Src"] = f"{cdn_prefix}{rec.isbn13}-{idx - 1}.jpg"
        row_i["Image Position"] = str(idx)
        row_i["Image Alt Text"] = f"Interior image {idx - 1}"

        rows.append(row_i)

    return rows


def run_scrape(args, mode_write_csv: bool) -> int:
    paths = get_paths()
    defaults = Defaults()
    run_id = new_run_id()
    run_ctx = RunContext(run_id=run_id)

    report = RunReport(
        run_id=run_id,
        input_source=args.input or args.isbn_file or "cli_isbns",
        template=args.template,
    )

    isbns = parse_isbns(args)
    if args.limit:
        isbns = isbns[: args.limit]

    # If input CSV is provided, we also capture vendor/designation
    vendor_map = {}
    designation_map = {}
    if args.input:
        candidates = read_candidates_from_csv(Path(args.input))
        vendor_map = {c.isbn13: c.vendor for c in candidates if c.vendor}
        designation_map = {c.isbn13: c.designation for c in candidates if c.designation}

    pw_cfg = PlaywrightConfig(
        headful=bool(args.headful),
        slowmo_ms=int(args.slowmo_ms or 0),
        timeout_ms=int(args.timeout_ms or 30000),
        trace=bool(args.trace),
    )

    edelweiss_cfg = get_edelweiss_config(paths)

    enriched: List[EnrichedRecord] = []

    with EdelweissClient(edelweiss_cfg, pw_cfg, paths, run_ctx) as ew:
        ew.login()

        for isbn13 in isbns:
            report.rows_total += 1

            url, opened = ew.search_isbn_and_open_title(isbn13)
            if not opened:
                shot = ew.screenshot(isbn13, "open_title_failed")
                report.rows_failed += 1
                report.failures.append(
                    __import__("src").models.Anomaly(  # avoid circular imports in this tiny scaffold
                        isbn13=isbn13,
                        stage="open_title",
                        message="Failed to open title card/modal (selectors likely need refinement)",
                        url=url,
                        screenshot_path=shot,
                    )
                )
                continue

            rec = extract_record_from_page(ew.page, isbn13, defaults)
            rec.source_url = url
            rec.vendor = vendor_map.get(isbn13) or rec.vendor
            # Collections: basic defaults
            rec.collections = ["Preorder"]
            if designation_map.get(isbn13):
                rec.collections.append(designation_map[isbn13])

            # --- Interior images ---
            if activate_images_tab(ew.page):
                try:
                    interior_urls = extract_interior_images(
                        ew.page,
                        max_images=defaults.interior_images_limit,
                    )
                    for u in interior_urls:
                        rec.images.append(ImageAsset(kind="interior", src_url=u))
                except Exception as e:
                    print(f"[images] interior extraction failed for {isbn13}: {e}")
            else:
                print(f"[images] skipping interior images for {isbn13} (IMAGES tab not activated)")

            # Download images
            if not args.no_images:
                download_images_for_record(rec, paths.assets_dir, defaults)

            # --- Normalize handle + canonical handle ---
            rec.handle = build_handle(rec.title)
            rec.canonical_handle = rec.handle

            # --- Normalize publication date ---
            # Ensure rec.pub_date is a date object or None
            from datetime import date, datetime

            if isinstance(rec.pub_date, str):
                try:
                    rec.pub_date = datetime.strptime(rec.pub_date, "%Y-%m-%d").date()
                except Exception:
                    rec.pub_date = None
            elif not isinstance(rec.pub_date, date):
                rec.pub_date = None

            if rec.pub_date is None:
                print(f"[warn] missing pub_date for ISBN {isbn13} (source: {rec.source_url})")

            # --- Clean and wrap preorder body HTML ---
            clean_body = clean_edelweiss_body_html(rec.body_html)
            rec.body_html = build_preorder_body(clean_body, rec.pub_date)

            # Validate
            anomaly = validate_record(rec)

            if anomaly and not args.no_images:
                anomaly.screenshot_path = ew.screenshot(isbn13, anomaly.stage)
                report.rows_failed += 1
                report.failures.append(anomaly)

            elif anomaly and args.no_images:
                # Cover-only / reconciliation mode:
                # accept minimally valid records so CSV rows are still written
                print(f"[warn] validation skipped for ISBN {isbn13} (no-images mode)")
                report.rows_ok += 1
                enriched.append(rec)

            else:
                report.rows_ok += 1
                enriched.append(rec)

    # Always write report
    report_path = (paths.outputs_dir / "run_report.json")
    report.to_json(report_path)

    # Build CSV if requested
    if mode_write_csv and args.template and args.out:
        template_path = Path(args.template)
        out_path = Path(args.out)

        # Get template columns
        import pandas as pd
        template_cols = list(pd.read_csv(template_path, dtype=str).fillna("").columns)

        rows: List[Dict[str, str]] = []
        for rec in enriched:
            rows.extend(build_rows_for_shopify_csv(rec, template_cols))

        write_output_csv_using_template(template_path, out_path, rows)

    return 0

def activate_images_tab(page) -> bool:
    """
    Reliably activate the IMAGES tab in the currently open Edelweiss drawer.
    Returns True if activated, False otherwise.
    """

    try:
        images_tab = page.locator(
            '[role="tab"]',
            has_text="Images"
        )

        if images_tab.count() == 0:
            return False

        # Click using Playwright's trusted click
        images_tab.first.click()

        # Wait until React confirms activation
        page.wait_for_function(
            """
            () => {
                const tab = [...document.querySelectorAll('[role="tab"]')]
                    .find(t => t.textContent.trim() === 'Images');
                return tab && tab.getAttribute('aria-selected') === 'true';
            }
            """,
            timeout=3000
        )

        return True

    except Exception:
        return False

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="preorder-bulk-importer")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_common(sp):
        sp.add_argument("--input", help="Candidate CSV (derives ISBNs + vendor/designation when present)")
        sp.add_argument("--isbn-file", help="Plain text file: one ISBN per line")
        sp.add_argument("--isbns", help="Comma-separated ISBNs, e.g. 978...,978...")
        sp.add_argument("--limit", type=int, default=0, help="Limit number of ISBNs")
        sp.add_argument("--dry-run", action="store_true", help="Do not write CSV (report still written)")
        sp.add_argument("--no-images", action="store_true", help="Do not download images")
        sp.add_argument("--headful", action="store_true", help="Run browser in headed mode")
        sp.add_argument("--slowmo-ms", type=int, default=0, help="Playwright slow motion delay per action")
        sp.add_argument("--timeout-ms", type=int, default=30000, help="Playwright default timeout for waits")
        sp.add_argument("--trace", action="store_true", help="Save Playwright trace zip to logs/playwright/")

    sp1 = sub.add_parser("scrape", help="Scrape/enrich and write run_report.json (no CSV unless explicitly requested)")
    add_common(sp1)

    sp2 = sub.add_parser("build-csv", help="Scrape/enrich and build Shopify import CSV matching template columns")
    add_common(sp2)
    sp2.add_argument("--template", required=True, help="Template CSV (goal example) used for column order/headers")
    sp2.add_argument("--out", required=True, help="Output CSV path")

    return p


def main() -> int:
    p = build_parser()
    args = p.parse_args()

    if args.cmd == "scrape":
        return run_scrape(args, mode_write_csv=False)

    if args.cmd == "build-csv":
        if args.dry_run:
            return run_scrape(args, mode_write_csv=False)
        return run_scrape(args, mode_write_csv=True)

    raise RuntimeError("Unknown command")


if __name__ == "__main__":
    raise SystemExit(main())