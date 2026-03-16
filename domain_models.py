from dataclasses import dataclass
from typing import List, Optional
from datetime import date
import re


DATE_TAG_PATTERN = re.compile(r"\d{2}-\d{2}-\d{4}")


def parse_date_tag(date_str: str) -> Optional[date]:
    try:
        month, day, year = map(int, date_str.split("-"))
        return date(year, month, day)
    except Exception:
        return None


@dataclass
class ProductMetadata:
    product_id: int
    tags: List[str]
    in_preorder_collection: bool
    date_tags_raw: List[str]
    pub_date_raw: Optional[date]
    override_date_raw: Optional[date]
    inventory: int
    title: Optional[str] = None
    vendor: Optional[str] = None
    isbn: Optional[str] = None

    def parsed_date_tags(self) -> List[date]:
        parsed = []
        for tag in self.date_tags_raw:
            if DATE_TAG_PATTERN.fullmatch(tag):
                dt = parse_date_tag(tag)
                if dt:
                    parsed.append(dt)
        return parsed

    def to_engine_input(self):
        """
        Convert domain model to engine input shape.
        Keeps engine pure and unaware of Shopify/domain layer.
        """
        return {
            "product_id": self.product_id,
            "tags": self.tags,
            "in_preorder_collection": self.in_preorder_collection,
            "date_tags": self.parsed_date_tags(),
            "pub_date": self.pub_date_raw,
            "override_date": self.override_date_raw,
            "inventory": self.inventory,
        }