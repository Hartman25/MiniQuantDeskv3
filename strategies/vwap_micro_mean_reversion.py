"""
VWAPMicroMeanReversion strategy.

Design goal:
- A "micro account" strategy ($100-$200) meant to validate correctness, controls,
  and execution integrity — not to chase alpha.

Core behavior:
- LONG-only mean reversion vs intraday VWAP
- Trades SPY only
- Time-gated (default 10:00–11:30 US/Eastern)
- Max 1 open position, max 1–2 trades/day
- Risk-based sizing with fixed $ risk per trade
- Hard daily loss limit disables the strategy for the rest of the day

IMPORTANT:
- Strategy emits StrategySignal intent objects (typed)
- Strategy never places orders directly
- Strategy does not read wall-clock; it uses bar.timestamp (and converts to US/Eastern)
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime, time, timezone
from typing import Optional, Dict, Any
from zoneinfo import ZoneInfo

from strategies.base import IStrategy, MarketDataContract
from strategies.signals import StrategySignal


EASTERN = ZoneInfo("America/New_York")


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _to_eastern(dt: datetime) -> datetime:
    return _to_utc(dt).astimezone(EASTERN)


@dataclass
class _VWAPState:
    trading_day: Optional[datetime.date] = None
    pv_sum: Decimal = Decimal("0")   # sum(price * volume)
    v_sum: Decimal = Decimal("0")    # sum(volume)


class VWAPMicroMeanReversion(IStrategy):
    """
    LONG-only mean reversion vs intraday VWAP for SPY.

    Config keys (with defaults):
      - vwap_min_bars: int = 20
      - entry_deviation_pct: Decimal = 0.003  (0.3%)
      - stop_loss_pct: Decimal = 0.003        (0.3%)
      - take_profit_pct: Decimal = 0.0015     (0.15%)  (optional helper, not required)
      - risk_dollars_per_trade: Decimal = 1.50
      - max_trades_per_day: int = 1
      - daily_loss_limit_usd: Decimal = 2.50
      - trade_start_time: "10:00" (ET)
      - trade_end_time: "11:30" (ET)
      - flat_time: "15:55" (ET)  (forces exit near close)
    """

    warmup_bars: int = 30

    def __init__(self, name: str, config: Dict[str, Any], symbols, timeframe: str = "1Min"):
        super().__init__(name=name, config=config, symbols=symbols, timeframe=timeframe)

        # Hard constraints
        self.symbols = ["SPY"]
        self.enabled = True

        # Parameters
        self.vwap_min_bars = int(config.get("vwap_min_bars", 20))
        self.entry_dev = Decimal(str(config.get("entry_deviation_pct", "0.003")))
        self.stop_loss_pct = Decimal(str(config.get("stop_loss_pct", "0.003")))
        self.take_profit_pct = Decimal(str(config.get("take_profit_pct", "0.0015")))
        self.risk_dollars = Decimal(str(config.get("risk_dollars_per_trade", "1.50")))
        self.max_trades_per_day = int(config.get("max_trades_per_day", 1))
        self.daily_loss_limit = Decimal(str(config.get("daily_loss_limit_usd", "2.50")))

        self.trade_start = self._parse_time(config.get("trade_start_time", "10:00"))
        self.trade_end = self._parse_time(config.get("trade_end_time", "11:30"))
        self.flat_time = self._parse_time(config.get("flat_time", "15:55"))

        self.entry_limit_offset_bps = int(config.get("entry_limit_offset_bps", 0))
        self.entry_limit_ttl_seconds = int(config.get("entry_limit_ttl_seconds", 90))

        # VWAP state
        self._vwap = _VWAPState()

        # Intraday counters / state (allowed by spec)
        self._bars_today = 0
        self._trades_today = 0
        self._disabled_today = False
        self._daily_pnl_est = Decimal("0")

        # Position tracking (updated via order events)
        self._in_position = False
        self._entry_price: Optional[Decimal] = None
        self._entry_qty: Optional[Decimal] = None
        self._last_entry_reason: str = ""

    # ---------------------------------------------------------------------
    # Helpers
    # ---------------------------------------------------------------------

    @staticmethod
    def _parse_time(val) -> time:
        if isinstance(val, time):
            return val
        s = str(val).strip()
        hh, mm = s.split(":")
        return time(int(hh), int(mm))

    def _reset_if_new_day(self, bar_dt_et: datetime) -> None:
        day = bar_dt_et.date()
        if self._vwap.trading_day != day:
            self._vwap = _VWAPState(trading_day=day)
            self._bars_today = 0
            self._trades_today = 0
            self._disabled_today = False
            self._daily_pnl_est = Decimal("0")
            self._in_position = False
            self._entry_price = None
            self._entry_qty = None

    def _update_vwap(self, bar: MarketDataContract) -> Optional[Decimal]:
        # Use typical price * volume when available; else close with unit weight.
        price = (bar.high + bar.low + bar.close) / Decimal("3")
        vol = Decimal(str(bar.volume)) if bar.volume is not None else Decimal("1")

        self._vwap.pv_sum += price * vol
        self._vwap.v_sum += vol
        self._bars_today += 1

        if self._bars_today < self.vwap_min_bars or self._vwap.v_sum == 0:
            return None
        return self._vwap.pv_sum / self._vwap.v_sum

    def _within_trade_window(self, t: time) -> bool:
        return (t >= self.trade_start) and (t <= self.trade_end)

    def _should_force_flat(self, t: time) -> bool:
        return t >= self.flat_time

    def _position_size(self, price: Decimal) -> Decimal:
        """
        Risk-based sizing:
          qty = risk_dollars / (price * stop_loss_pct)

        Fractional shares supported (Decimal).
        """
        if price <= 0:
            return Decimal("0")

        risk_per_share = price * self.stop_loss_pct
        if risk_per_share <= 0:
            return Decimal("0")

        qty = self.risk_dollars / risk_per_share

        # Micro-account guardrails: keep notional reasonable
        # Default: cap to $50 notional to avoid oversized position in tiny accounts
        max_notional = Decimal(str(self.config.get("max_notional_usd", "50")))
        if qty * price > max_notional:
            qty = max_notional / price

        # Round to 0.001 shares (Alpaca fractional tolerance)
        return qty.quantize(Decimal("0.001"))

    # ---------------------------------------------------------------------
    # Strategy interface
    # ---------------------------------------------------------------------

    def on_init(self) -> None:
        self.enabled = True
        self.log_info("VWAPMicroMeanReversion initialized")

    def on_bar(self, bar: MarketDataContract) -> Optional[StrategySignal]:
        if not self.enabled:
            return None

        bar_dt_et = _to_eastern(bar.timestamp)
        self._reset_if_new_day(bar_dt_et)

        # Hard disable after daily loss limit (estimated)
        if self._disabled_today:
            return None

        # Update VWAP
        vwap = self._update_vwap(bar)
        if vwap is None:
            return None  # warmup

        now_t = bar_dt_et.time()

        # Force flat near close
        if self._in_position and self._should_force_flat(now_t):
            return StrategySignal(
                symbol=bar.symbol,
                side="SELL",
                quantity=self._entry_qty or Decimal("0"),
                order_type="MARKET",
                entry_price=bar.close,
                reason="FORCE_FLAT_EOD",
                strategy=self.name,
            )

        # If in position: exit on mean reversion or stop
        if self._in_position and self._entry_price and self._entry_qty:
            # Stop loss (price-based)
            stop_price = self._entry_price * (Decimal("1") - self.stop_loss_pct)
            if bar.close <= stop_price:
                # Estimate loss as risk dollars (micro account)
                self._daily_pnl_est -= self.risk_dollars
                if abs(self._daily_pnl_est) >= self.daily_loss_limit:
                    self._disabled_today = True
                return StrategySignal(
                    symbol=bar.symbol,
                    side="SELL",
                    quantity=self._entry_qty,
                    order_type="MARKET",
                    entry_price=bar.close,
                    reason="STOP_LOSS",
                    strategy=self.name,
                )

            # Take profit / mean reversion to VWAP
            if bar.close >= vwap:
                # conservative: estimate small win
                self._daily_pnl_est += (self.risk_dollars * Decimal("0.5"))
                return StrategySignal(
                    symbol=bar.symbol,
                    side="SELL",
                    quantity=self._entry_qty,
                    order_type="MARKET",
                    entry_price=bar.close,
                    reason="MEAN_REVERSION_TO_VWAP",
                    strategy=self.name,
                )

            return None

        # Not in position: check entry conditions
        if not self._within_trade_window(now_t):
            return None

        if self._trades_today >= self.max_trades_per_day:
            return None

        threshold = vwap * (Decimal("1") - self.entry_dev)
        if bar.close < threshold:
            qty = self._position_size(bar.close)
            if qty <= 0:
                return None

            self._trades_today += 1
            self._last_entry_reason = f"PRICE_BELOW_VWAP_BY_{self.entry_dev}"
            return StrategySignal(
                symbol=bar.symbol,
                side="BUY",
                quantity=qty,
                order_type="LIMIT",
                entry_price=bar.close,
                limit_price=(
                    bar.close
                    * (Decimal("1") - (Decimal(str(self.entry_limit_offset_bps)) / Decimal("10000")))
                ),
                ttl_seconds=self.entry_limit_ttl_seconds,
                stop_loss=(bar.close * (Decimal("1") - self.stop_loss_pct)),
                take_profit=None,  # exit via mean reversion logic
                reason=self._last_entry_reason,
                strategy=self.name,
            )

        return None

    def on_order_filled(self, order_id: str, symbol: str, filled_qty: Decimal, fill_price: Decimal):
        # Minimal state updates only; persistent truth is in PositionStore.
        # Since the interface doesn't include side, infer via current state.
        if not self._in_position:
            # assume entry fill
            self._in_position = True
            self._entry_price = fill_price
            self._entry_qty = filled_qty
        else:
            # assume exit fill
            self._in_position = False
            self._entry_price = None
            self._entry_qty = None
        return None

    def on_order_rejected(self, order_id: str, symbol: str, reason: str):
        # If rejected, consider the "trade" not taken.
        return None

    def on_stop(self) -> None:
        self.enabled = False
