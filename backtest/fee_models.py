"""
Fee models - commission and transaction cost simulation.

LEAN COMPATIBILITY:
Based on QuantConnect's IFeeModel.

ARCHITECTURE:
- Equity fees (per-share or percentage)
- Options fees (per-contract)
- Futures fees (per-contract)
- Forex fees (spread-based)
- Crypto fees (maker/taker)

Extensible for any fee structure.
"""

from typing import Optional
from decimal import Decimal
from abc import ABC, abstractmethod
from enum import Enum

from core.brokers import BrokerOrderSide
from backtest.fill_models import AssetClass


# ============================================================================
# FEE MODELS
# ============================================================================

class FeeModel(ABC):
    """
    Base fee model.
    
    LEAN equivalent: IFeeModel
    
    Calculates transaction costs including:
    - Commissions
    - Exchange fees
    - Regulatory fees
    - Financing costs
    """
    
    @abstractmethod
    def get_fee(
        self,
        asset_class: AssetClass,
        side: BrokerOrderSide,
        quantity: Decimal,
        price: Decimal
    ) -> Decimal:
        """
        Calculate total fees.
        
        Args:
            asset_class: Asset class
            side: BUY or SELL
            quantity: Order quantity
            price: Fill price
            
        Returns:
            Total fee amount (positive)
        """
        pass


class InteractiveBrokersFeeModel(FeeModel):
    """
    Interactive Brokers fee structure.
    
    EQUITIES:
    - $0.005 per share
    - $1.00 minimum per order
    - $0.01 maximum (0.5% of trade value)
    
    OPTIONS:
    - $0.65 per contract
    - $1.00 minimum per order
    
    Based on IBKR Lite/Pro pricing.
    """
    
    def get_fee(
        self,
        asset_class: AssetClass,
        side: BrokerOrderSide,
        quantity: Decimal,
        price: Decimal
    ) -> Decimal:
        """Calculate IB-style fees."""
        
        if asset_class == AssetClass.EQUITY:
            # $0.005 per share
            fee = quantity * Decimal("0.005")
            
            # Minimum $1.00
            fee = max(fee, Decimal("1.00"))
            
            # Maximum 0.5% of trade value
            trade_value = quantity * price
            max_fee = trade_value * Decimal("0.005")
            fee = min(fee, max_fee)
            
            return fee
        
        elif asset_class == AssetClass.OPTION:
            # $0.65 per contract
            fee = quantity * Decimal("0.65")
            
            # Minimum $1.00
            fee = max(fee, Decimal("1.00"))
            
            return fee
        
        elif asset_class == AssetClass.FUTURE:
            # $0.85 per contract (typical)
            return quantity * Decimal("0.85")
        
        elif asset_class == AssetClass.CRYPTO:
            # 0.2% maker, 0.5% taker (assume taker)
            trade_value = quantity * price
            return trade_value * Decimal("0.005")
        
        else:
            # Default: 0.1% of trade value
            trade_value = quantity * price
            return trade_value * Decimal("0.001")


class AlpacaFeeModel(FeeModel):
    """
    Alpaca commission structure.
    
    EQUITIES:
    - $0 commission (commission-free)
    - Payment for order flow
    - SEC fees on sells
    
    Based on current Alpaca pricing.
    """
    
    def get_fee(
        self,
        asset_class: AssetClass,
        side: BrokerOrderSide,
        quantity: Decimal,
        price: Decimal
    ) -> Decimal:
        """Calculate Alpaca-style fees."""
        
        if asset_class == AssetClass.EQUITY:
            # Commission-free base
            fee = Decimal("0")
            
            # SEC fee on sells only: $22.10 per $1,000,000
            if side == BrokerOrderSide.SELL:
                trade_value = quantity * price
                sec_fee = trade_value * Decimal("0.0000221")
                fee += sec_fee
            
            return fee
        
        elif asset_class == AssetClass.CRYPTO:
            # Alpaca crypto: 0.25% maker/taker
            trade_value = quantity * price
            return trade_value * Decimal("0.0025")
        
        else:
            # Other assets: assume zero
            return Decimal("0")


class ConstantFeeModel(FeeModel):
    """
    Simple constant fee model.
    
    Useful for:
    - Testing
    - Simplified backtests
    - Custom fee structures
    """
    
    def __init__(
        self,
        per_share: Optional[Decimal] = None,
        per_contract: Optional[Decimal] = None,
        percentage: Optional[Decimal] = None,
        minimum: Decimal = Decimal("0")
    ):
        """
        Initialize constant fee.
        
        Args:
            per_share: Fee per share (equities)
            per_contract: Fee per contract (options/futures)
            percentage: Fee as % of trade value
            minimum: Minimum fee per order
        """
        self.per_share = per_share
        self.per_contract = per_contract
        self.percentage = percentage
        self.minimum = minimum
    
    def get_fee(
        self,
        asset_class: AssetClass,
        side: BrokerOrderSide,
        quantity: Decimal,
        price: Decimal
    ) -> Decimal:
        """Calculate constant fee."""
        fee = Decimal("0")
        
        # Per-share fee
        if self.per_share and asset_class == AssetClass.EQUITY:
            fee = quantity * self.per_share
        
        # Per-contract fee
        elif self.per_contract and asset_class in (AssetClass.OPTION, AssetClass.FUTURE):
            fee = quantity * self.per_contract
        
        # Percentage fee
        elif self.percentage:
            trade_value = quantity * price
            fee = trade_value * self.percentage
        
        # Apply minimum
        fee = max(fee, self.minimum)
        
        return fee


class ZeroFeeModel(FeeModel):
    """
    Zero commission model.
    
    Useful for:
    - Theoretical backtests
    - Strategy comparison
    - Ignoring transaction costs
    """
    
    def get_fee(
        self,
        asset_class: AssetClass,
        side: BrokerOrderSide,
        quantity: Decimal,
        price: Decimal
    ) -> Decimal:
        """Return zero fees."""
        return Decimal("0")
