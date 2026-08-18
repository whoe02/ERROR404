"""Test fixture for Autocure — contains one intentional, unguarded bug.

send_receipt() uses order.customer_email without checking for None first.
Calling it with customer_email=None raises a real exception.
"""

from dataclasses import dataclass, field


@dataclass
class Order:
    id: int
    customer_email: str | None
    items: list[dict] = field(default_factory=list)
    discount_code: str | None = None


def calculate_total(order: Order) -> int:
    subtotal = sum(item["price_cents"] * item["qty"] for item in order.items)
    if order.discount_code:
        subtotal = int(subtotal * 0.9)
    return subtotal


def send_receipt(order: Order) -> str:
    # INTENTIONAL BUG: no None-check on customer_email before using it.
    return f"Receipt sent to {order.customer_email.lower()}"
