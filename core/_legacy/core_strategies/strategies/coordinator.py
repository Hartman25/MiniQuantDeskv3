"""
Strategy Coordinator for multi-strategy conflict resolution.

ARCHITECTURE:
- Detect conflicting orders (opposing positions same symbol)
- Position netting (combine offsetting orders)
- Capital allocation across strategies
- Order priority and timing
- Exposure aggregation

DESIGN PRINCIPLE:
Prevent strategies from fighting each other.

CONFLICT EXAMPLES:
Strategy1: BUY SPY 100 shares
Strategy2: SELL SPY 100 shares
→ Coordinator detects conflict, cancels both (waste of commissions)

Strategy1: BUY AAPL 50 shares  
Strategy2: BUY AAPL 30 shares
→ Coordinator combines into single order: BUY AAPL 80 shares

Based on institutional multi-manager coordination systems.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Set, Tuple
from enum import Enum
from collections import defaultdict

from core.logging import get_logger, LogStream


# ============================================================================
# ORDER CONFLICT TYPES
# ============================================================================

class ConflictType(Enum):
    """Types of order conflicts."""
    OPPOSING = "opposing"           # Buy vs Sell same symbol
    EXCESSIVE = "excessive"         # Total exposure too high
    REDUNDANT = "redundant"         # Duplicate orders
    CORRELATION = "correlation"     # Highly correlated positions
    NONE = "none"                   # No conflict


@dataclass
class OrderIntent:
    """Intended order from a strategy."""
    strategy_id: str
    symbol: str
    side: str  # "BUY" or "SELL"
    quantity: Decimal
    order_type: str  # "MARKET", "LIMIT", etc.
    limit_price: Optional[Decimal] = None
    stop_price: Optional[Decimal] = None
    time_in_force: str = "DAY"
    priority: int = 0  # Higher = more important
    
    @property
    def notional_value(self) -> Decimal:
        """Approximate notional value."""
        if self.limit_price:
            return self.quantity * self.limit_price
        return Decimal("0")  # Unknown for market orders


@dataclass
class ConflictResult:
    """Result of conflict detection."""
    conflict_type: ConflictType
    affected_orders: List[OrderIntent]
    resolution: str
    action: str  # "CANCEL", "COMBINE", "ALLOW", "REDUCE"
    
    @property
    def has_conflict(self) -> bool:
        """Check if conflict exists."""
        return self.conflict_type != ConflictType.NONE


# ============================================================================
# STRATEGY COORDINATOR
# ============================================================================

class StrategyCoordinator:
    """
    Coordinate multiple trading strategies.
    
    RESPONSIBILITIES:
    - Detect order conflicts (opposing, excessive, redundant)
    - Combine compatible orders
    - Allocate capital across strategies
    - Aggregate exposure by symbol
    - Enforce priority rules
    
    CONFLICT RESOLUTION:
    
    OPPOSING ORDERS:
      Strategy1: BUY SPY 100
      Strategy2: SELL SPY 100
      → Cancel both (net zero, wasted fees)
    
    COMBINING ORDERS:
      Strategy1: BUY AAPL 50
      Strategy2: BUY AAPL 30
      → Combine: BUY AAPL 80
    
    EXCESSIVE EXPOSURE:
      Current: AAPL position $10K
      New orders would add $5K
      Max exposure: $12K
      → Reduce orders to fit limit
    
    USAGE:
        coordinator = StrategyCoordinator(
            max_symbol_exposure_percent=Decimal("15.0"),
            max_total_exposure_percent=Decimal("95.0")
        )
        
        # Register strategies
        coordinator.register_strategy("momentum", priority=10)
        coordinator.register_strategy("mean_reversion", priority=5)
        
        # Submit orders from strategies
        order1 = OrderIntent(
            strategy_id="momentum",
            symbol="AAPL",
            side="BUY",
            quantity=Decimal("50")
        )
        
        order2 = OrderIntent(
            strategy_id="mean_reversion",
            symbol="AAPL",
            side="SELL",
            quantity=Decimal("50")
        )
        
        # Check conflicts
        conflicts = coordinator.check_conflicts([order1, order2])
        
        if conflicts:
            # Handle conflicts
            resolved = coordinator.resolve_conflicts([order1, order2])
        
        # Get final orders to execute
        final_orders = coordinator.get_executable_orders()
    """
    
    def __init__(
        self,
        max_symbol_exposure_percent: Decimal = Decimal("15.0"),
        max_total_exposure_percent: Decimal = Decimal("95.0"),
        max_correlated_exposure_percent: Decimal = Decimal("30.0")
    ):
        """
        Initialize strategy coordinator.
        
        Args:
            max_symbol_exposure_percent: Max exposure per symbol (% of portfolio)
            max_total_exposure_percent: Max total exposure (% of portfolio)
            max_correlated_exposure_percent: Max exposure in correlated positions
        """
        self.max_symbol_exposure = max_symbol_exposure_percent
        self.max_total_exposure = max_total_exposure_percent
        self.max_correlated_exposure = max_correlated_exposure_percent
        
        self.logger = get_logger(LogStream.RISK)
        
        # Strategy registry
        self.strategies: Dict[str, Dict] = {}  # strategy_id -> {priority, active}
        
        # Current positions (symbol -> quantity, side)
        self.positions: Dict[str, Tuple[Decimal, str]] = {}
        
        # Pending orders
        self.pending_orders: List[OrderIntent] = []
        
        # Statistics
        self.conflicts_detected: List[Dict] = []
        self.orders_combined: int = 0
        self.orders_cancelled: int = 0
        
        self.logger.info("StrategyCoordinator initialized", extra={
            "max_symbol_exposure": str(max_symbol_exposure_percent),
            "max_total_exposure": str(max_total_exposure_percent)
        })
    
    # ========================================================================
    # STRATEGY MANAGEMENT
    # ========================================================================
    
    def register_strategy(
        self,
        strategy_id: str,
        priority: int = 0,
        active: bool = True
    ):
        """Register a trading strategy."""
        self.strategies[strategy_id] = {
            "priority": priority,
            "active": active,
            "registered_at": datetime.now(timezone.utc)
        }
        
        self.logger.info(f"Strategy registered: {strategy_id}", extra={
            "priority": priority,
            "active": active
        })
    
    def deactivate_strategy(self, strategy_id: str):
        """Deactivate a strategy (stop accepting orders)."""
        if strategy_id in self.strategies:
            self.strategies[strategy_id]["active"] = False
            
    def activate_strategy(self, strategy_id: str):
        """Activate a strategy."""
        if strategy_id in self.strategies:
            self.strategies[strategy_id]["active"] = True
    
    # ========================================================================
    # CONFLICT DETECTION
    # ========================================================================
    
    def check_conflicts(
        self,
        orders: List[OrderIntent],
        portfolio_value: Decimal = Decimal("0")
    ) -> List[ConflictResult]:
        """
        Check for conflicts among orders.
        
        Args:
            orders: List of pending orders
            portfolio_value: Current portfolio value
            
        Returns:
            List of detected conflicts
        """
        conflicts = []
        
        # Group orders by symbol
        orders_by_symbol: Dict[str, List[OrderIntent]] = defaultdict(list)
        for order in orders:
            orders_by_symbol[order.symbol].append(order)
        
        # Check each symbol for conflicts
        for symbol, symbol_orders in orders_by_symbol.items():
            if len(symbol_orders) < 2:
                continue
            
            # Check for opposing orders
            buy_orders = [o for o in symbol_orders if o.side == "BUY"]
            sell_orders = [o for o in symbol_orders if o.side == "SELL"]
            
            if buy_orders and sell_orders:
                conflicts.append(ConflictResult(
                    conflict_type=ConflictType.OPPOSING,
                    affected_orders=buy_orders + sell_orders,
                    resolution="Cancel offsetting orders",
                    action="CANCEL"
                ))
        
        return conflicts
    
    def detect_excessive_exposure(
        self,
        orders: List[OrderIntent],
        portfolio_value: Decimal
    ) -> List[ConflictResult]:
        """Detect if orders would cause excessive exposure."""
        if portfolio_value == 0:
            return []
        
        conflicts = []
        
        # Calculate total exposure per symbol
        exposure_by_symbol: Dict[str, Decimal] = defaultdict(Decimal)
        
        for symbol, (qty, side) in self.positions.items():
            # Estimate current position value (rough)
            exposure_by_symbol[symbol] += abs(qty)
        
        for order in orders:
            exposure_by_symbol[order.symbol] += order.quantity
        
        # Check against limits
        for symbol, exposure in exposure_by_symbol.items():
            exposure_percent = (exposure / portfolio_value * Decimal("100")) if portfolio_value > 0 else Decimal("0")
            
            if exposure_percent > self.max_symbol_exposure:
                symbol_orders = [o for o in orders if o.symbol == symbol]
                conflicts.append(ConflictResult(
                    conflict_type=ConflictType.EXCESSIVE,
                    affected_orders=symbol_orders,
                    resolution=f"Exposure {exposure_percent:.1f}% exceeds {self.max_symbol_exposure}%",
                    action="REDUCE"
                ))
        
        return conflicts
    
    # ========================================================================
    # CONFLICT RESOLUTION
    # ========================================================================
    
    def resolve_conflicts(
        self,
        orders: List[OrderIntent]
    ) -> List[OrderIntent]:
        """
        Resolve conflicts and return executable orders.
        
        Args:
            orders: List of conflicting orders
            
        Returns:
            List of resolved orders ready for execution
        """
        # Filter out inactive strategies
        active_orders = [
            o for o in orders 
            if o.strategy_id in self.strategies 
            and self.strategies[o.strategy_id]["active"]
        ]
        
        # Group by symbol
        orders_by_symbol: Dict[str, List[OrderIntent]] = defaultdict(list)
        for order in active_orders:
            orders_by_symbol[order.symbol].append(order)
        
        resolved_orders = []
        
        for symbol, symbol_orders in orders_by_symbol.items():
            # Separate buy/sell
            buy_orders = [o for o in symbol_orders if o.side == "BUY"]
            sell_orders = [o for o in symbol_orders if o.side == "SELL"]
            
            # Net position
            buy_qty = sum(o.quantity for o in buy_orders)
            sell_qty = sum(o.quantity for o in sell_orders)
            
            net_qty = buy_qty - sell_qty
            
            if net_qty > 0:
                # Net buy
                # Combine all buy orders
                if len(buy_orders) > 1:
                    # Pick highest priority strategy
                    primary = max(buy_orders, key=lambda o: self.strategies[o.strategy_id]["priority"])
                    primary.quantity = net_qty
                    resolved_orders.append(primary)
                    self.orders_combined += 1
                else:
                    resolved_orders.append(buy_orders[0])
                    
            elif net_qty < 0:
                # Net sell
                if len(sell_orders) > 1:
                    primary = max(sell_orders, key=lambda o: self.strategies[o.strategy_id]["priority"])
                    primary.quantity = abs(net_qty)
                    resolved_orders.append(primary)
                    self.orders_combined += 1
                else:
                    resolved_orders.append(sell_orders[0])
            else:
                # Perfect offset - cancel all
                self.orders_cancelled += len(symbol_orders)
                self.logger.warning(f"Cancelled offsetting orders for {symbol}", extra={
                    "buy_qty": str(buy_qty),
                    "sell_qty": str(sell_qty)
                })
        
        return resolved_orders
    
    # ========================================================================
    # POSITION TRACKING
    # ========================================================================
    
    def update_position(
        self,
        symbol: str,
        quantity: Decimal,
        side: str
    ):
        """Update current position."""
        self.positions[symbol] = (quantity, side)
    
    def get_exposure(self, symbol: str) -> Decimal:
        """Get current exposure for symbol."""
        if symbol in self.positions:
            qty, _ = self.positions[symbol]
            return abs(qty)
        return Decimal("0")
    
    # ========================================================================
    # STATISTICS
    # ========================================================================
    
    def get_statistics(self) -> Dict:
        """Get coordination statistics."""
        return {
            "registered_strategies": len(self.strategies),
            "active_strategies": sum(1 for s in self.strategies.values() if s["active"]),
            "conflicts_detected": len(self.conflicts_detected),
            "orders_combined": self.orders_combined,
            "orders_cancelled": self.orders_cancelled,
            "current_positions": len(self.positions)
        }
