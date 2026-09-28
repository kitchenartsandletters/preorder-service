# NYT Reporting — The Two-Week Model (authoritative)

**Status: authoritative reference.** This is the owner's definition of how NYT
reporting weeks work, written down because the ambiguity between the two "weeks"
has caused repeated confusion and at least one near-miss double-report. When
anything about "this week" in NYT reporting is unclear, this doc is the tiebreak.

Last updated: 2026-09-28

---

## Two different "weeks" — never conflate them

NYT reporting involves two Sunday–Saturday weeks, offset by exactly 7 days:

1. **Report cycle week** — the calendar week the CSV is *generated and
   submitted in*. The report can be generated as early as **Sunday 12:00am** of
   this week and is **due Tuesday 12:00pm (noon)** of this same week.
2. **Sales week (the reported week)** — the **prior** completed Sunday–Saturday
   week. This is the sales data the CSV contains.

**Worked example (the one that caused the confusion, 2026-09-28):**
- Today is Monday **Sep 28**, inside the **cycle week Sep 27 – Oct 3**.
- The report due **Tue Sep 29 at noon** reports the **sales week Sep 20 – 26**.
- It could have been generated as early as **Sun Sep 27, 12:00am**.

## What the report contains

For the sales week (e.g. Sep 20–26):
- **Regular weekly Shopify sales** for that window (net of refunds), for all
  products.
- **Plus** cumulative preorder quantity for any title whose **pub date falls
  within that same sales week**. So a title publishing Sep 22 (inside Sep 20–26)
  has its cumulative presale qty added to the Sep 20–26 report.

This is why *Bistro: French Food and Wine for Every Occasion* (ISBN
9781846016905, pub **Sep 22**) belonged in — and was in — the Sep 20–26 report
filed on Sep 28/29. Its pub date is inside the sales week.

## How the code computes this (verified 2026-09-28)

`jobs/nyt_reporter.py::run()`:
```python
queue_start, queue_end = _current_week_bounds()   # cycle week   (Sep 27 – Oct 3)
sales_start, sales_end = _sales_week_bounds()      # sales week   (Sep 20 – 26)
```
- `_current_week_bounds()` = the current Sunday–Saturday week.
- `_sales_week_bounds()` = `_current_week_bounds()` minus 7 days.
- The CSV is generated for the **sales week**. Preorder inclusion uses pub date
  in the sales week. **This is correct** and matches the model above.

## The known defect: `release_report_week_start` can disagree with the CSV

**Symptom:** a title correctly included in a sales-week CSV can carry a
`release_report_week_start` for a *different* week, making it look like it
belongs to a later report. This is what nearly caused *Bistro* to be left
unmarked (and thus re-reported the following week — a double count to NYT).

**Cause:** `routes/admin_preorders.py::mark_reported` stamps
`release_report_week_start = resolve_week_bounds(payload.week_anchor)` — the
Sunday–Saturday week containing whatever `week_anchor` the dashboard sends **at
queue time**. If a title is queued during the cycle week (e.g. Sep 28, anchor in
the Sep 27 week) for sales that report in the *prior* week (Sep 20–26), the field
records the **cycle** week while the CSV uses the **sales** week. The two are 7
days apart.

So `release_report_week_start` currently means "the week the queue click
happened in," **not** "the sales-week report this title appears in." Two titles
in the *same* filed CSV can have different `release_report_week_start` values
depending on which day each was queued.

## Operating rule until the defect is fixed

**The filed CSV (`nyt_report_log.csv_content`) is the authority for what was
reported — not `release_report_week_start`.**

To answer "was this title reported for week W?", check whether its ISBN is in
that week's `csv_content`:
```sql
SELECT csv_content LIKE '%' || :isbn || '%'
FROM preorder.nyt_report_log
WHERE week_start = :sales_week_start AND week_end = :sales_week_end;
```
Do **not** rely on `release_report_week_start` to decide report membership.

Likewise, when reconciling `release_state.nyt_uploaded_at` after a run, mark the
titles that are **in the filed CSV**, not the ones matching a given
`release_report_week_start`.

## The fix (for whoever next touches the reporter)

Make `release_report_week_start` mean "the sales-week report this title appears
in," consistently, regardless of when it was queued:

- In `mark_reported`, derive each title's report week from **its pub date**
  (the Sunday–Saturday week containing `effective_pub_date`) — or from the
  current sales week — rather than from `payload.week_anchor` / queue time.
- Then `release_report_week_start` agrees with what the CSV actually does, and
  `_fetch_queued_titles` / `_mark_titles_uploaded` scoping (which currently pulls
  and marks the whole unreported-queue superset regardless of week) can be made
  week-accurate against the same definition.

Coordinate with the dashboard queue action (`admin-dashboard`), which supplies
`week_anchor`. Add tests asserting a title queued in the cycle week for a
prior-week pub date gets the sales-week `release_report_week_start`.

## Related

- The reporter's upload-confirmation model (why "already submitted" is success)
  is documented in the header of `jobs/nyt_reporter.py` (PR #32).
- `_fetch_queued_titles` marks the entire unreported queue regardless of week —
  noted above; fix alongside the week-stamping so marking and generation agree.
