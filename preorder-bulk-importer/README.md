# preorder-bulk-importer

Bulk-enrich preorder candidate titles (Winter/Spring 2026) via Edelweiss, download images locally, and generate a Shopify-ready import CSV (matching a known-good template).

This project lives inside:
`preorder-service/preorder-bulk-importer/`

---

## What this does

1) Ingest candidate ISBNs from one of:
- an **input CSV** (recommended)
- a plain **ISBN file** (one ISBN per line)
- CLI arg list: `--isbns 978...,978...`

2) Use **Playwright (Chromium)** to:
- log into Edelweiss using env vars: `EDELWEISS_USER`, `EDELWEISS_PASS`
- search each ISBN
- open the title detail modal
- extract title/authors/pub date/binding/price/body_html + image URLs
- download cover image + first 5 interior images to a deterministic folder

3) Emit:
- `outputs/preorder_products_import_ready.csv`
- `outputs/run_report.json` with anomalies/confirmations needed

---

## Inputs and Outputs

### Inputs
Place your CSVs in:
`inputs/`

- `inputs/Winter Spring 2026 Preorder - For Upload.csv` (candidates)
- `inputs/preorder_products_export.csv` (goal template example / header reference)

### Outputs
- `outputs/preorder_products_import_ready.csv`
- `outputs/run_report.json`

### Assets
Images are downloaded to:
`assets/edelweiss/{isbn13}/`
- `cover.jpg`
- `interior_01.jpg` ... `interior_05.jpg`

---

## Setup

### 1) Create and activate venv
```bash
python -m venv .venv
source .venv/bin/activate

## Repo Folder Tree

preorder-service/preorder-bulk-importer/
  README.md
  requirements.txt
  .env.example
  src/
    __init__.py
    config.py
    models.py
    io_csv.py
    cli.py
    edelweiss/
      __init__.py
      selectors.py
      client.py
      parser.py
      images.py
    reporting/
      __init__.py
      anomalies.py
      run_report.py

## CLI Shapes

Option A: Read ISBNs from input CSV (recommended)
python -m src.cli build-csv \
  --input inputs/"Winter Spring 2026 Preorder - For Upload.csv" \
  --template inputs/preorder_products_export.csv \
  --out outputs/preorder_products_import_ready.csv

Option B: Provide an ISBN file
python -m src.cli build-csv \
  --isbn-file inputs/isbn_list.txt \
  --template inputs/preorder_products_export.csv \
  --out outputs/preorder_products_import_ready.csv

Option C: Provide ISBNs via CLI args
python -m src.cli build-csv \
  --isbns 9781648294075,9781234567890 \
  --template inputs/preorder_products_export.csv \
  --out outputs/preorder_products_import_ready.csv

Dry run (no CSV write) + limit
python -m src.cli scrape \
  --input inputs/"Winter Spring 2026 Preorder - For Upload.csv" \
  --limit 2 \
  --dry-run

## Playwright reliability controls

The scraper defaults to explicit waits (selectors + modal readiness) rather than pure sleeps, but you can optionally add:
	•	--headful : open a real visible browser
	•	--slowmo-ms 150 : slows each action (debug)
	•	--timeout-ms 30000 : global timeout for waits
	•	--trace : saves a trace zip per run in logs/playwright/

python -m src.cli build-csv \
  --input inputs/"Winter Spring 2026 Preorder - For Upload.csv" \
  --template inputs/preorder_products_export.csv \
  --out outputs/preorder_products_import_ready.csv \
  --limit 5 \
  --headful \
  --slowmo-ms 150 \
  --timeout-ms 45000 \
  --trace

## Next steps
	•	Shopify GraphQL push mode can be added behind a CLI flag (--mode shopify), using staged uploads for images and productCreate.
	•	Gemini-based SEO description shortening is not included in this draft scaffold; a stub function exists for later integration.

