from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, List, Dict


@dataclass
class CandidateRow:
    # minimal normalized candidate row derived from input CSV
    isbn13: str
    vendor: Optional[str] = None
    designation: Optional[str] = None  # e.g. capsule/collection designation from input
    raw: Dict[str, str] = field(default_factory=dict)


@dataclass
class ImageAsset:
    kind: str  # "cover" or "interior"
    src_url: str
    local_path: Optional[str] = None


@dataclass
class EnrichedRecord:
    isbn13: str

    title: Optional[str] = None
    seo_title: Optional[str] = None
    authors_display: Optional[str] = None

    pub_date: Optional[str] = None          # YYYY-MM-DD
    pub_date_tag: Optional[str] = None      # MM-DD-YYYY

    binding_label: Optional[str] = None     # Hardcover/Paperback/etc
    binding_tag: Optional[str] = None       # C/P/F/S

    price_usd: Optional[str] = None         # "35.00"
    body_html: Optional[str] = None

    # Shopify-ish defaults (set by builder)
    product_type: Optional[str] = None
    language_tag: Optional[str] = None
    weight_lbs: Optional[float] = None
    country_of_origin: Optional[str] = None
    hs_code: Optional[str] = None

    vendor: Optional[str] = None
    collections: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)

    images: List[ImageAsset] = field(default_factory=list)

    source_url: Optional[str] = None  # for audit/debug


@dataclass
class Anomaly:
    isbn13: str
    stage: str
    message: str
    url: Optional[str] = None
    screenshot_path: Optional[str] = None
    extra: Dict[str, str] = field(default_factory=dict)