from __future__ import annotations

import re
from typing import List, Optional, Tuple
from datetime import datetime

from playwright.sync_api import Page

from ..models import EnrichedRecord, ImageAsset
from ..config import Defaults
from . import selectors as S


_MONTHS = {
    "January": 1, "February": 2, "March": 3, "April": 4,
    "May": 5, "June": 6, "July": 7, "August": 8,
    "September": 9, "October": 10, "November": 11, "December": 12,
}


def _trim_seo_title(full_title: str) -> str:
    # First pass rule: keep before ":"; else keep before "—"
    if ":" in full_title:
        return full_title.split(":", 1)[0].strip()
    if "—" in full_title:
        return full_title.split("—", 1)[0].strip()
    return full_title.strip()


def _parse_pub_date(text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Edelweiss date semantics:

    We want the product release date. Edelweiss may show multiple dates.

    Priority order:
      1) "On Sale Date: <Mon D, YYYY>"
      2) "Pub Date: <Mon D, YYYY>"
      3) A standalone pipe-delimited date token: "| <Mon D, YYYY> |"

    Notes:
      - "Ship Date" is not the release date and is ignored.
      - Returns (YYYY-MM-DD, MM-DD-YYYY) or (None, None).
    """
    if not text:
        return (None, None)

    def _coerce(raw: str) -> Tuple[Optional[str], Optional[str]]:
        raw = (raw or "").strip()
        if not raw:
            return (None, None)
        for fmt in ("%B %d, %Y", "%b %d, %Y"):
            try:
                dt = datetime.strptime(raw, fmt)
                return (dt.strftime("%Y-%m-%d"), dt.strftime("%m-%d-%Y"))
            except ValueError:
                continue
        return (None, None)

    # --- Stage 1: Explicit labels (preferred) ---
    # Examples:
    #   "On Sale Date: Feb 10, 2026"
    #   "Pub Date: Mar 24, 2026"
    m = re.search(
        r"(On Sale Date|Pub Date):\s*([A-Za-z]{3,9}\s+\d{1,2},\s+\d{4})",
        text,
    )
    if m:
        return _coerce(m.group(2))

    # --- Stage 2: Standalone pipe-delimited date token ---
    # Examples:
    #   "| Feb 10, 2026 | On Sale Date: Feb 10, 2026 | Ship Date: Jan 14, 2026"
    #   "| May 13, 2026 | Ship Date: Jun 4, 2026"
    m = re.search(
        r"\|\s*([A-Za-z]{3,9}\s+\d{1,2},\s+\d{4})\s*\|",
        text,
    )
    if m:
        return _coerce(m.group(1))

    return (None, None)


def _parse_price_usd(text: str) -> Optional[str]:
    m = re.search(r"\$(\d+(?:\.\d{2})?)\s*USD", text)
    return m.group(1) if m else None


def _parse_binding(text: str) -> Tuple[Optional[str], Optional[str]]:
    # examples: "Hardcover Paper over boards"
    t = (text or "").strip()
    if not t:
        return (None, None)

    # Determine label + tag
    if t.lower().startswith("hardcover"):
        return ("Hardcover", "C")
    if t.lower().startswith("paperback"):
        return ("Paperback", "P")
    if "flexibound" in t.lower():
        return ("Flexibound", "F")
    if "spiral" in t.lower():
        return ("Spiralbound", "S")

    # fallback: unknown
    return (None, None)


def _normalize_authors(contrib_text: str) -> Optional[str]:
    """
    From:
      "Devin Finigan, Peter Kaminsky (With), Erin French (Foreword by)"
    To:
      "Devin Finigan with Peter Kaminsky"
    """
    if not contrib_text:
        return None

    parts = [p.strip() for p in contrib_text.split(",") if p.strip()]

    # Exclude foreword/introduction roles
    excluded = []
    kept = []
    for p in parts:
        pl = p.lower()
        if "(foreword" in pl or "(introduction" in pl:
            excluded.append(p)
            continue
        kept.append(p)

    primary = None
    with_author = None
    others = []

    for p in kept:
        if "(with)" in p.lower():
            name = re.sub(r"\s*\(with\)\s*", "", p, flags=re.I).strip()
            with_author = name
        elif primary is None:
            primary = p.strip()
        else:
            others.append(p.strip())

    if primary and with_author:
        return f"{primary} with {with_author}"

    # If no explicit with, return first one (or join)
    if primary and others:
        return ", ".join([primary] + others)
    return primary


def extract_record_from_page(page: Page, isbn13: str, defaults: Defaults) -> EnrichedRecord:
    rec = EnrichedRecord(isbn13=isbn13)

    # Title
    title_el = page.locator(S.TITLE_TEXT)
    if title_el.count() > 0:
        full_title = title_el.first.inner_text()

        # Normalize whitespace and line breaks
        full_title = full_title.replace("\xa0", " ")
        full_title = re.sub(r"\s*\n\s*", ": ", full_title)
        full_title = re.sub(r"\s{2,}", " ", full_title).strip()

        # Strip trailing edition / bracketed metadata
        # Examples: "(1st Edition)", "(1st Edition, New edition)", "[A Cookbook]"
        full_title = re.sub(r"\s*[\(\[].*?[\)\]]\s*$", "", full_title).strip()

        rec.title = full_title
        rec.seo_title = _trim_seo_title(full_title)

    # Contributors
    contrib_el = page.locator(S.CONTRIBUTORS)
    if contrib_el.count() > 0:
        rec.authors_display = _normalize_authors(contrib_el.first.inner_text().strip())

    # Scan dotDot blocks to find key strings
    dotdot_texts: List[str] = []
    for i in range(page.locator(S.DOTDOT).count()):
        t = page.locator(S.DOTDOT).nth(i).inner_text().strip()
        if t:
            dotdot_texts.append(t)

    combined = " | ".join(dotdot_texts)

    # Pub date from "On Sale Date" or "Pub Date"
    pub_date, pub_tag = _parse_pub_date(combined)
    rec.pub_date = pub_date
    rec.pub_date_tag = pub_tag
    print(f"[debug] parsed pub_date={rec.pub_date} pub_tag={rec.pub_date_tag} for ISBN {isbn13}")

    # Price USD
    rec.price_usd = _parse_price_usd(combined)

    # Binding: try to find the dotDot line containing Hardcover/Paperback
    binding_line = next((t for t in dotdot_texts if "hardcover" in t.lower() or "paperback" in t.lower() or "flexibound" in t.lower() or "spiral" in t.lower()), "")
    label, tag = _parse_binding(binding_line)
    rec.binding_label = label
    rec.binding_tag = tag

    # Body HTML: using tabpanel content
    body_el = page.locator(S.BODY_TABPANEL_CONTENT)
    if body_el.count() > 0:
        # We capture inner HTML so Shopify body_html can preserve markup
        try:
            rec.body_html = body_el.first.inner_html()
        except Exception:
            # fallback to text
            rec.body_html = body_el.first.inner_text()

    # Cover image URL
    cover = page.locator(S.COVER_IMG)
    if cover.count() > 0:
        src = cover.first.get_attribute("src") or ""
        if src:
            rec.images.append(ImageAsset(kind="cover", src_url=src))

    # Defaults
    rec.product_type = defaults.product_type
    rec.language_tag = defaults.language_tag
    rec.weight_lbs = defaults.weight_lbs
    rec.country_of_origin = defaults.country_of_origin
    rec.hs_code = defaults.hs_code

    # Tags (base)
    if rec.language_tag:
        rec.tags.append(rec.language_tag)
    if rec.pub_date_tag:
        rec.tags.append(rec.pub_date_tag)
    if rec.binding_tag:
        rec.tags.append(rec.binding_tag)

    return rec


def extract_interior_image_urls(page: Page, limit: int) -> List[str]:
    """
    Click Images tab and scrape thumbnail image src urls.
    """
    # click Images tab via xpath button text match
    img_tab = page.locator(f"xpath={S.IMAGES_TAB_BUTTON_XPATH}")
    if img_tab.count() > 0:
        img_tab.first.click()

    thumbs = page.locator(S.IMAGES_THUMBNAILS)
    out = []
    for i in range(min(thumbs.count(), limit)):
        src = thumbs.nth(i).get_attribute("src") or ""
        if src:
            out.append(src)
    return out