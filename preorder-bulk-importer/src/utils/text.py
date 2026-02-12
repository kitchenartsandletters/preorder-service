import re
import unicodedata
from datetime import date
from typing import Optional


def build_handle(title: str) -> str:
    """
    Build a Shopify-safe handle from a book title.

    Rules:
    - Trim subtitle (split on ':' or '—')
    - Normalize unicode to ASCII
    - Lowercase
    - Remove punctuation
    - Replace spaces with hyphens
    - Collapse multiple hyphens
    - Strip leading/trailing hyphens
    """
    if not title:
        return ""

    # 1. Trim subtitle
    base = re.split(r"[:—]", title, maxsplit=1)[0]

    # 2. Normalize unicode → ASCII
    normalized = (
        unicodedata.normalize("NFKD", base)
        .encode("ascii", "ignore")
        .decode("ascii")
    )

    # 3. Lowercase
    normalized = normalized.lower()

    # 4. Remove punctuation (keep letters, numbers, spaces)
    normalized = re.sub(r"[^a-z0-9\s-]", "", normalized)

    # 5. Replace whitespace with hyphens
    normalized = re.sub(r"\s+", "-", normalized)

    # 6. Collapse multiple hyphens
    normalized = re.sub(r"-{2,}", "-", normalized)

    return normalized.strip("-")

def clean_edelweiss_body_html(raw_html: str) -> str:
    """
    Remove Edelweiss-specific wrapper markup while preserving
    publisher-authored HTML content.
    """
    if not raw_html:
        return ""

    html = raw_html.strip()

    # Remove leading Edelweiss wrapper divs
    html = re.sub(
        r'^<div[^>]*MuiBox-root[^>]*>\s*<div>\s*',
        "",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )

    # Remove trailing Edelweiss wrapper closing
    html = re.sub(
        r'\s*<br>\s*&nbsp;\s*</div>\s*</div>\s*$',
        "",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )

    return html.strip()

def build_preorder_body(clean_html: str, pub_date: Optional[date | str]) -> str:
    """
    Build the final preorder body HTML with a preamble and footer.

    Notes:
    - pub_date may be:
        * datetime.date
        * ISO string YYYY-MM-DD
        * None (edge cases / parsing gaps)
    - If pub_date is missing or invalid, we omit the release-date sentence
      but still generate a valid preorder body.
    """

    if not clean_html:
        clean_html = ""

    pub_date_obj: Optional[date] = None

    if isinstance(pub_date, date):
        pub_date_obj = pub_date
    elif isinstance(pub_date, str):
        try:
            pub_date_obj = date.fromisoformat(pub_date)
        except ValueError:
            pub_date_obj = None

    # --- Build preamble ---
    if pub_date_obj:
        pub_date_str = pub_date_obj.strftime("%B %d, %Y")
        preamble = (
            f'<p><b>This is a *Featured Preorder for a title releasing on {pub_date_str}.</b></p>'
            f'<p><em><strong>This is what the publisher tells us about this book:</strong></em></p>'
        )
    else:
        # Fallback: still valid preorder copy, no date claim
        preamble = (
            '<p><b>This is a *Featured Preorder title.</b></p>'
            '<p><em><strong>This is what the publisher tells us about this book:</strong></em></p>'
        )

    footer = (
        '<p><b>* Featured Preorder books earn you an <i>extra </i>'
        '150 rewards points, good for $5 off a future purchase. '
        'If you\'re not already a member of our rewards program you can '
        '<a href="https://www.kitchenartsandletters.com/pages/reward">'
        '<b>learn more about all the ways to earn points and sign up here.</b>'
        '</a> It\'s as simple as creating a store account.</b></p>'
    )

    return "\n".join([preamble, clean_html, footer]).strip()