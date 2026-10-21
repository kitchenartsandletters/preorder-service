#!/usr/bin/env python3
"""
nyt_recon_sunday.py — observe the NYT upload flow BEFORE submitting.

Drives login → upload page → sets the real CSV on #filename, capturing a
screenshot + DOM dump at each stage. STOPS before the Upload click by default
(the Upload click is what files the data and locks the week).

  python scripts/nyt_recon_sunday.py           # observe only, never submits
  python scripts/nyt_recon_sunday.py --submit  # ALSO clicks Upload (real submission!)

Run from repo root. Reuses the reporter's CSV generation and login.
"""
import os, sys, argparse, base64
from datetime import datetime
from dotenv import load_dotenv

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_ROOT, ".env"))
sys.path.insert(0, _ROOT)

from playwright.sync_api import sync_playwright
from jobs.nyt_reporter import (
    _get_supabase, _current_week_bounds, _sales_week_bounds,
    _fetch_queued_titles, _fetch_presale_qtys, _fetch_product_metadata,
    _fetch_shopify_week_sales, _generate_csv,
)

NYT_USER = os.environ["NYT_PORTAL_USERNAME"]
NYT_PASS = os.environ["NYT_PORTAL_PASSWORD"]

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recon_out")
os.makedirs(OUT, exist_ok=True)

def dump(page, frame, label):
    ts = datetime.now().strftime("%H%M%S")
    png = os.path.join(OUT, f"{ts}_{label}.png")
    page.screenshot(path=png, full_page=True)
    print(f"\n===== {label}  (screenshot: {png}) =====")
    try:
        print("--- PAGE body (first 600) ---")
        print(page.inner_text("body")[:600])
    except Exception as e:
        print(f"(page body err: {e})")
    if frame is not None:
        try:
            print("--- IFRAME body (first 800) ---")
            print(frame.locator("body").inner_text()[:800])
            btns = frame.locator("button, input[type=submit], input[type=button]")
            n = btns.count()
            print(f"--- IFRAME buttons: {n} ---")
            for i in range(n):
                b = btns.nth(i)
                try:
                    print(f"  [{i}] text={ (b.inner_text() or '').strip()[:50]!r}"
                          f" value={b.get_attribute('value')!r}"
                          f" name={b.get_attribute('name')!r}"
                          f" id={b.get_attribute('id')!r}"
                          f" visible={b.is_visible()}")
                except Exception as e:
                    print(f"  [{i}] <err {e}>")
        except Exception as e:
            print(f"(iframe err: {e})")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--submit", action="store_true",
                    help="ALSO click Upload — this is a REAL submission that locks the week")
    args = ap.parse_args()

    # Build the real CSV (what would actually be submitted)
    sb = _get_supabase()
    qs, qe = _current_week_bounds()
    ss, se = _sales_week_bounds()
    print(f"queue week {qs}→{qe}   sales week {ss}→{se}")
    queued = _fetch_queued_titles(sb, qs, qe)
    pids = [int(r["product_id"]) for r in queued]
    print(f"queued titles: {len(queued)}")
    presales = _fetch_presale_qtys(sb, pids) if pids else {}
    week_sales = _fetch_shopify_week_sales(ss, se)
    metadata = _fetch_product_metadata(sb, pids) if pids else {}
    csv_text, csv_filename, rows = _generate_csv(queued, presales, week_sales, metadata, se, sb)
    print(f"CSV: {rows} rows → {csv_filename}")

    tmp = os.path.join(OUT, csv_filename)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(csv_text)
    print(f"CSV written locally: {tmp}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)   # visible
        page = browser.new_context().new_page()

        page.goto("https://bestsellers.nytimes.com/login", wait_until="domcontentloaded")
        dump(page, None, "01_login_page")
        page.get_by_placeholder("Enter username").fill(NYT_USER)
        page.get_by_placeholder("Password").fill(NYT_PASS)
        page.get_by_role("button", name="Sign in").click()
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(1500)
        dump(page, None, "02_after_login")

        page.get_by_role("link", name="Upload Spreadsheet").click()
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(2500)
        frame = page.locator("#spreadsheet_frame").content_frame
        dump(page, frame, "03_upload_page_PRE_upload")

        # Set the file but DO NOT click Upload (yet)
        try:
            frame.locator("#filename").set_input_files(tmp)
            page.wait_for_timeout(1000)
            dump(page, frame, "04_file_set_PRE_upload_click")
        except Exception as e:
            print(f"(set_input_files err: {e})")

        if args.submit:
            print("\n*** --submit: clicking Upload — THIS FILES THE DATA ***")
            frame.get_by_role("button", name="Upload").click()
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(3000)
            dump(page, frame, "05_AFTER_upload_click")
        else:
            print("\nObserve-only: Upload NOT clicked. Nothing submitted.")

        print("\nBrowser stays open 10 min. Inspect freely. Ctrl-C to exit.")
        page.wait_for_timeout(600000)
        browser.close()

if __name__ == "__main__":
    main()