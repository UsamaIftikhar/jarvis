"""Product catalog tools — read/write the Khas Bazaar product catalog."""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from .registry import MARKETING_REGISTRY, MarketingToolEntry

logger = logging.getLogger("jarvis.marketing.catalog")

_CATALOG_PATH = os.path.join(os.path.dirname(__file__), "..", "catalog", "products.json")


def _load_catalog() -> dict[str, Any]:
    with open(_CATALOG_PATH, encoding="utf-8") as f:
        return json.load(f)


def product_display_name(product_id: str) -> str:
    """Human-readable product name for voice/UI replies."""
    product_id = (product_id or "").strip()
    if not product_id:
        return ""
    for product in _load_catalog().get("products", []):
        if product.get("id") == product_id:
            return str(product.get("name") or product_id.replace("-", " ").title())
    return product_id.replace("-", " ").title()


def _save_catalog(catalog: dict[str, Any]) -> None:
    with open(_CATALOG_PATH, "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2)


async def _list_products(args: dict[str, Any]) -> str:
    try:
        catalog = _load_catalog()
        products = catalog.get("products", [])
        category = str(args.get("category", "") or "").strip()
        if category:
            products = [p for p in products if p.get("category") == category]
        if not products:
            return f"No products found{' in category: ' + category if category else ''}."
        lines = [f"Khas Bazaar Products ({len(products)} total):"]
        for p in products:
            status = p.get("status", "active")
            price = f"Rs {p['price']}" if p.get("price") else "price TBD"
            lines.append(f"• [{p['id']}] {p['name']} — {p['category']} — {price} — {status}")
        return "\n".join(lines)
    except Exception as exc:
        logger.exception("list_products failed")
        return f"Catalog error: {exc}"


async def _get_product(args: dict[str, Any]) -> str:
    product_id = str(args.get("product_id", "") or "").strip()
    if not product_id:
        return "Provide a product_id."
    try:
        catalog = _load_catalog()
        for p in catalog.get("products", []):
            if p["id"] == product_id:
                return json.dumps(p, ensure_ascii=False, indent=2)
        return f"Product '{product_id}' not found. Use list_products to see available IDs."
    except Exception as exc:
        logger.exception("get_product failed")
        return f"Catalog error: {exc}"


async def _add_product(args: dict[str, Any]) -> str:
    required = ["id", "name", "category", "description"]
    for field in required:
        if not args.get(field):
            return f"Missing required field: {field}"
    try:
        catalog = _load_catalog()
        existing_ids = {p["id"] for p in catalog.get("products", [])}
        if args["id"] in existing_ids:
            return f"Product ID '{args['id']}' already exists. Use a unique ID."
        new_product: dict[str, Any] = {
            "id": args["id"],
            "name": args["name"],
            "urdu_name": args.get("urdu_name", ""),
            "category": args["category"],
            "description": args["description"],
            "style_tags": args.get("style_tags", []),
            "colors": args.get("colors", []),
            "price": args.get("price"),
            "status": "active",
            "content_angles": args.get("content_angles", []),
            "scroll_stop_hooks": args.get("hooks", []),
            "best_formats": args.get("best_formats", []),
            "seasonal": args.get("seasonal", ["year-round"]),
        }
        catalog["products"].append(new_product)
        _save_catalog(catalog)
        return f"Product '{args['name']}' added successfully with ID '{args['id']}'."
    except Exception as exc:
        logger.exception("add_product failed")
        return f"Catalog error: {exc}"


async def _update_product_price(args: dict[str, Any]) -> str:
    product_id = str(args.get("product_id", "") or "").strip()
    price = args.get("price")
    if not product_id or price is None:
        return "Provide product_id and price."
    try:
        catalog = _load_catalog()
        for p in catalog["products"]:
            if p["id"] == product_id:
                p["price"] = price
                _save_catalog(catalog)
                return f"Price for '{p['name']}' updated to Rs {price}."
        return f"Product '{product_id}' not found."
    except Exception as exc:
        logger.exception("update_product_price failed")
        return f"Catalog error: {exc}"


async def _get_brand_info(_args: dict[str, Any]) -> str:
    try:
        catalog = _load_catalog()
        brand = catalog.get("brand", {})
        return json.dumps(brand, ensure_ascii=False, indent=2)
    except Exception as exc:
        return f"Catalog error: {exc}"


# ---------------------------------------------------------------------------
# Register tools
# ---------------------------------------------------------------------------

MARKETING_REGISTRY.register(MarketingToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "list_products",
            "description": "List all Khas Bazaar products in the catalog. Optionally filter by category.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "Filter by: vases, planters, decor-sets, dried-florals, figurines (optional)"},
                },
                "required": [],
            },
        },
    },
    handler=_list_products,
    thinking_label="Browsing product catalog…",
    terminal=False,
    help_hint="lists all Khas Bazaar products",
))

MARKETING_REGISTRY.register(MarketingToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "get_product",
            "description": "Get full details of a specific product including content angles, hooks, and style tags.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "string", "description": "The product ID e.g. 'gold-rim-ribbed-set-nude'"},
                },
                "required": ["product_id"],
            },
        },
    },
    handler=_get_product,
    thinking_label="Loading product details…",
    terminal=False,
    help_hint="returns full product data including content angles",
))

MARKETING_REGISTRY.register(MarketingToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "add_product",
            "description": "Add a new product to the Khas Bazaar catalog.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id":          {"type": "string", "description": "Unique kebab-case ID e.g. 'marble-tray-white'"},
                    "name":        {"type": "string", "description": "Product display name"},
                    "urdu_name":   {"type": "string", "description": "Urdu name (optional)"},
                    "category":    {"type": "string", "description": "Category: vases / planters / decor-sets / dried-florals / figurines / trays / candles"},
                    "description": {"type": "string", "description": "Full product description"},
                    "style_tags":  {"type": "array",  "description": "Style tags e.g. ['boho', 'minimal', 'gold']"},
                    "colors":      {"type": "array",  "description": "Color list"},
                    "price":       {"type": "number", "description": "Price in PKR (optional, can set later)"},
                    "content_angles": {"type": "array", "description": "Content angle ideas"},
                    "hooks":       {"type": "array",  "description": "Scroll-stop hook lines"},
                    "best_formats": {"type": "array", "description": "Best content formats"},
                    "seasonal":    {"type": "array",  "description": "Seasonal relevance tags"},
                },
                "required": ["id", "name", "category", "description"],
            },
        },
    },
    handler=_add_product,
    thinking_label="Adding product to catalog…",
    terminal=True,
    help_hint="adds a new product to the Khas Bazaar catalog",
))

MARKETING_REGISTRY.register(MarketingToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "update_product_price",
            "description": "Update the price of an existing product.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "string", "description": "Product ID"},
                    "price":      {"type": "number", "description": "New price in PKR"},
                },
                "required": ["product_id", "price"],
            },
        },
    },
    handler=_update_product_price,
    thinking_label="Updating product price…",
    terminal=True,
    help_hint="updates price for a product",
))

MARKETING_REGISTRY.register(MarketingToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "get_brand_info",
            "description": "Get Khas Bazaar brand identity: name, aesthetic, target audience, tone, hashtag strategy.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    handler=_get_brand_info,
    thinking_label="Loading brand profile…",
    terminal=False,
    help_hint="returns Khas Bazaar brand identity and strategy",
))
