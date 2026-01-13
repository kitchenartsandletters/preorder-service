1️⃣ Canonical column mapping (authoritative)

Below is what we should populate, what we should explicitly set, and what we should leave blank.

I’ll group by Shopify section so it’s auditable.

⸻

🔹 Core product fields

Column	Value
Handle	blank (let Shopify derive OR future canonical_handle logic)
Title	rec.title
Body (HTML)	rec.body_html
Vendor	rec.vendor (from input CSV)
Product Category	Media > Books > Print Books
Type	BOOK
Published	FALSE
Status	draft

⚠️ Important: Published must be FALSE and Status = draft
This ensures no accidental storefront exposure.

⸻

🔹 Tags & collections

Column	Value
Tags	comma-joined:
Ln_En, rec.pub_date_tag, rec.binding_tag, plus preorder capsule tags	
Collections	⚠️ NOT set via CSV column — handled post-import or by collection rules


⸻

🔹 Variant definition (single-variant product)

Column	Value
Option1 Name	Title
Option1 Value	Default
Variant SKU	rec.authors_display
Variant Barcode	rec.isbn13
Variant Price	rec.price_usd
Variant Compare At Price	(blank)
Variant Inventory Tracker	shopify
Variant Inventory Policy	continue
Variant Fulfillment Service	manual
Variant Requires Shipping	TRUE
Variant Taxable	TRUE
Variant Grams	2268 (≈ 5 lb × 453.592)
Variant Weight Unit	lb
Variant Tax Code	(blank or book-specific if you add later)
Cost per item	(blank)


⸻

🔹 Images (CSV-safe approach)

Cover image only in CSV
Interior images are better uploaded later via API or Media UI.

Column	Value
Image Src	cover.src_url (not local path)
Image Position	1
Image Alt Text	Book Cover: {SEO title}
Variant Image	(blank)

✅ This avoids Shopify CSV quirks where multiple image rows = duplicate variants.

⸻

🔹 SEO

Column	Value
SEO Title	rec.seo_title
SEO Description	(Gemini-generated later — leave blank for now)


⸻

🔹 Google Shopping (explicit neutral defaults)

Column	Value
Google Shopping / Google Product Category	Media > Books
Google Shopping / Condition	new
Google Shopping / Custom Product	FALSE

(All other Google columns blank.)

⸻

🔹 Custom metafields (important)

These map exactly to your headers — no guessing.

Column	Value
Author (product.metafields.custom.author)	rec.authors_display
Binding (product.metafields.custom.binding)	rec.binding_label
Language (product.metafields.custom.language)	English
Publication Date (product.metafields.custom.pub_date)	rec.pub_date
Canonical Handle (product.metafields.custom.canonical_handle)	(blank for now)

Everything else remains blank by design.

⸻

🔹 Shipping / compliance constants

Column	Value
Gift Card	FALSE
Variant Requires Shipping	TRUE
Variant Taxable	TRUE


⸻

2️⃣ Explicit “do not populate” list (important discipline)

These must stay blank in v1:
	•	All Growave metafields
	•	Reviews / ratings metafields
	•	Shopify discovery recommendation metafields
	•	Region / cuisine / specialization
	•	Inventory logs
	•	Preorder override date
	•	Reserve / shelf / location

This keeps the importer purely additive and prevents corruption of downstream systems.

⸻

3️⃣ Required changes to build_rows_for_shopify_csv()

When I patch, I will:

✔️ Add hard mappings for:
	•	Published
	•	Status
	•	Product Category
	•	Variant inventory behavior
	•	Weight in grams
	•	Shipping + taxable flags

✔️ Stop setting:
	•	Any placeholder metafield names not present in your real headers

✔️ Ensure:
	•	Only columns present in template_columns are written
	•	Everything else remains empty strings

⸻

4️⃣ One subtle but critical Shopify CSV rule (FYI)

Because we’re doing single-variant products, this is correct:

Option1 Name = Title
Option1 Value = Default

If either is missing, Shopify may:
	•	auto-create a “Default Title” option, or
	•	reject the row silently

We’re explicitly controlling this.

⸻