from __future__ import annotations

from typing import Optional

from ..models import EnrichedRecord, Anomaly


def validate_record(rec: EnrichedRecord) -> Optional[Anomaly]:
    """
    Returns first anomaly found (simple version).
    In a later phase we can collect multiple anomalies per record.
    """
    if not rec.title:
        return Anomaly(isbn13=rec.isbn13, stage="parse_title", message="Title missing", url=rec.source_url)
    if not rec.pub_date:
        return Anomaly(isbn13=rec.isbn13, stage="parse_pub_date", message="On Sale Date missing/unparsed", url=rec.source_url)
    if not rec.price_usd:
        return Anomaly(isbn13=rec.isbn13, stage="parse_price", message="USD price missing/unparsed", url=rec.source_url)
    if not rec.binding_tag or not rec.binding_label:
        return Anomaly(isbn13=rec.isbn13, stage="parse_binding", message="Binding missing/unparsed", url=rec.source_url)
    cover = next((a for a in rec.images if a.kind == "cover"), None)
    if not cover or not cover.src_url:
        return Anomaly(isbn13=rec.isbn13, stage="parse_cover", message="Cover image missing", url=rec.source_url)
    if not rec.body_html:
        return Anomaly(isbn13=rec.isbn13, stage="parse_body_html", message="Body HTML missing/empty", url=rec.source_url)
    return None