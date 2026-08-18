"""Test fixture for Autocure — contains one intentional, unguarded bug.

apply_discount() looks up order.discount_code in _DISCOUNT_RATES with no
fallback for an unknown code. Calling it with any code other than SAVE10 or
SAVE20 raises a real KeyError.
"""

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
