"""
Product Search Tool
====================
Searches for a product by name/keyword and reports whether it exists.

Uses DummyJSON (https://dummyjson.com) -- a free, public, no-auth REST API
built for exactly this kind of demo/prototype use. It has a real search
endpoint over ~190 sample products with titles, brands, prices, and stock
levels, and returns clean JSON directly -- no scraping, no bot protection,
no headless browser required.

To point this at a real production catalog later, swap this module's
implementation for whatever your actual product API/search endpoint is --
the ProductSearchResult shape and search_product() signature can stay the
same so nothing else in the bot needs to change.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import requests

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10.0
SEARCH_URL = "https://dummyjson.com/products/search"


@dataclass
class ProductSearchResult:
    query: str
    search_url: str
    found: bool
    result_count: int
    message: str
    products: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "search_url": self.search_url,
            "found": self.found,
            "result_count": self.result_count,
            "message": self.message,
            "products": self.products,
        }


def search_product(query: str, timeout: float = DEFAULT_TIMEOUT) -> ProductSearchResult:
    """
    Search for *query* (a product name or keyword) and report whether it
    exists, using DummyJSON's public search endpoint.
    """
    params = {"q": query}

    try:
        resp = requests.get(SEARCH_URL, params=params, timeout=timeout)
        resp.raise_for_status()
    except requests.exceptions.RequestException as exc:
        logger.error("Product search request failed: %s", exc)
        return ProductSearchResult(
            query=query,
            search_url=resp.url if "resp" in dir() else SEARCH_URL,
            found=False,
            result_count=0,
            message=f"Search request failed: {exc}",
        )

    data = resp.json()
    total = int(data.get("total", 0))
    items = data.get("products", [])

    products = [
        {
            "id": item.get("id"),
            "title": item.get("title"),
            "brand": item.get("brand"),
            "price": item.get("price"),
            "stock": item.get("stock"),
            "availability_status": item.get("availabilityStatus"),
        }
        for item in items
    ]

    return ProductSearchResult(
        query=query,
        search_url=resp.url,
        found=total > 0,
        result_count=total,
        message=(
            f"Found {total} matching product(s)."
            if total > 0
            else "No products found for this search."
        ),
        products=products,
    )