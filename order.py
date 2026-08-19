"""Test fixture for Autocure — contains five independent, unguarded bugs,
each reachable via its own function so they can be triggered one at a time."""

from dataclasses import dataclass, field


@dataclass
class Order:
    id: int
    customer_email: str | None
    items: list[dict] = field(default_factory=list)
    discount_code: str | None = None


_DISCOUNT_RATES = {
    "SAVE10": 0.10,
    "SAVE20": 0.20,
}


def calculate_total(order: Order) -> int:
    subtotal = sum(item["price_cents"] * item["qty"] for item in order.items)
    if order.discount_code:
        subtotal = int(subtotal * 0.9)
    return subtotal


def apply_discount(order: Order) -> int:
    subtotal = sum(item["price_cents"] * item["qty"] for item in order.items)
    if not order.discount_code:
        return subtotal
    # INTENTIONAL BUG: unknown discount codes raise KeyError instead of
    # being rejected or ignored gracefully.
    rate = _DISCOUNT_RATES[order.discount_code]
    return int(subtotal * (1 - rate))


def send_receipt(order: Order) -> str:
    # Handle missing email gracefully
    if order.customer_email is None:
        return "Receipt not sent: customer email missing"
    return f"Receipt sent to {order.customer_email.lower()}"


def calculate_average_item_price(order: Order) -> int:
    subtotal = sum(item["price_cents"] * item["qty"] for item in order.items)
    # INTENTIONAL BUG: no guard for an empty cart, raises ZeroDivisionError.
    if len(order.items) == 0:
        return 0
    return subtotal // len(order.items)


def extract_email_domain(order: Order) -> str:
    # INTENTIONAL BUG: assumes the email always contains "@", raises
    # IndexError on a malformed address.
    return order.customer_email.split("@")[1]


def summarize_items(order: Order) -> str:
    # INTENTIONAL BUG: joins raw dicts instead of formatting them first,
    # raises TypeError.
    return ", ".join(order.items)


def get_first_item_sku(order: Order) -> str:
    # INTENTIONAL BUG: assumes every item dict has a "sku" key, raises
    # KeyError.
    if not order.items:
        raise ValueError("Order has no items")
    if "sku" not in order.items[0]:
        raise ValueError("First item missing 'sku' key")
    return order.items[0]["sku"]
