import sys
sys.path.insert(0, r'C:\Users\Zacha\Desktop\MiniQuantDeskv2')

from core.state.order_machine import OrderStateMachine, Order, OrderStatus
from decimal import Decimal

print("SUCCESS: OrderStateMachine with Order storage imports correctly")
print()

methods = [m for m in dir(OrderStateMachine) if not m.startswith('_')]
print(f"OrderStateMachine methods: {methods}")
print()

# Quick functionality test
print("Testing Order creation...")
order = Order(
    order_id="TEST_001",
    symbol="SPY",
    quantity=Decimal("10"),
    side="LONG",
    order_type="MARKET",
    strategy="test"
)

print(f"Order created: {order.order_id}")
print(f"Order state: {order.state.value}")
print(f"Order is_active: {order.is_active}")
print(f"Order is_pending: {order.is_pending}")
print()

print("ALL TESTS PASSED")
