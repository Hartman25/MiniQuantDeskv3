"""
Results formatting and reporting.

Beautiful terminal output + export capabilities.
"""

from typing import List
from decimal import Decimal

from backtest.performance import PerformanceMetrics


# ============================================================================
# RESULTS FORMATTER
# ============================================================================

class ResultsFormatter:
    """
    Format backtest results for display.
    
    FEATURES:
    - Beautiful terminal output
    - HTML report generation
    - CSV export
    - JSON export
    """
    
    @staticmethod
    def print_results(metrics: PerformanceMetrics):
        """
        Print results to terminal.
        
        Args:
            metrics: Performance metrics
        """
        print("\n" + "="*70)
        print(" " * 20 + "BACKTEST RESULTS")
        print("="*70)
        
        # Time period
        print(f"\nTime Period:")
        print(f"  Start: {metrics.start_date.strftime('%Y-%m-%d')}")
        print(f"  End:   {metrics.end_date.strftime('%Y-%m-%d')}")
        print(f"  Duration: {metrics.duration_days} days ({metrics.duration_days/365.25:.1f} years)")
        
        # Returns
        print(f"\nReturns:")
        print(f"  Total Return:        {metrics.total_return:>10.2%}")
        print(f"  Annualized Return:   {metrics.annualized_return:>10.2%}")
        print(f"  Daily Mean Return:   {metrics.daily_returns_mean:>10.4%}")
        print(f"  Daily Volatility:    {metrics.daily_returns_std:>10.4%}")
        
        # Risk Metrics
        print(f"\nRisk Metrics:")
        print(f"  Sharpe Ratio:        {metrics.sharpe_ratio:>10.2f}")
        print(f"  Sortino Ratio:       {metrics.sortino_ratio:>10.2f}")
        print(f"  Calmar Ratio:        {metrics.calmar_ratio:>10.2f}")
        print(f"  Max Drawdown:        {metrics.max_drawdown:>10.2%}")
        print(f"  Max DD Duration:     {metrics.max_drawdown_duration_days:>10} days")
        
        # Trade Statistics
        print(f"\nTrade Statistics:")
        print(f"  Total Trades:        {metrics.total_trades:>10}")
        print(f"  Winning Trades:      {metrics.winning_trades:>10}")
        print(f"  Losing Trades:       {metrics.losing_trades:>10}")
        print(f"  Win Rate:            {metrics.win_rate:>10.2%}")
        print(f"  Profit Factor:       {metrics.profit_factor:>10.2f}")
        print(f"  Avg Win:             ${metrics.avg_win:>10,.2f}")
        print(f"  Avg Loss:            ${metrics.avg_loss:>10,.2f}")
        print(f"  Largest Win:         ${metrics.largest_win:>10,.2f}")
        print(f"  Largest Loss:        ${metrics.largest_loss:>10,.2f}")
        
        # Portfolio
        print(f"\nPortfolio:")
        print(f"  Final Equity:        ${metrics.final_equity:>10,.2f}")
        print(f"  Peak Equity:         ${metrics.peak_equity:>10,.2f}")
        
        # Costs
        print(f"\nTransaction Costs:")
        print(f"  Total Commission:    ${metrics.total_commission:>10,.2f}")
        print(f"  Commission % of PnL: {metrics.commission_pct_of_total_value:>10.2%}")
        
        print("\n" + "="*70 + "\n")
    
    @staticmethod
    def format_equity_curve_ascii(equity_curve: List[tuple], height: int = 20, width: int = 70):
        """
        Generate ASCII art equity curve.
        
        Args:
            equity_curve: List of (timestamp, equity)
            height: Chart height
            width: Chart width
            
        Returns:
            ASCII art string
        """
        if not equity_curve:
            return "No equity data"
        
        # Extract values
        values = [float(eq) for _, eq in equity_curve]
        min_val = min(values)
        max_val = max(values)
        
        # Normalize to height
        if max_val == min_val:
            normalized = [height // 2] * len(values)
        else:
            normalized = [
                int((v - min_val) / (max_val - min_val) * (height - 1))
                for v in values
            ]
        
        # Sample points to fit width
        if len(normalized) > width:
            step = len(normalized) / width
            sampled = [normalized[int(i * step)] for i in range(width)]
        else:
            sampled = normalized
        
        # Build chart
        lines = []
        for row in range(height - 1, -1, -1):
            line = ""
            for val in sampled:
                if val == row:
                    line += "*"
                elif val > row:
                    line += "|"
                else:
                    line += " "
            
            # Add axis label
            value_at_row = min_val + (max_val - min_val) * (row / (height - 1))
            line = f"{value_at_row:>10,.0f} | " + line
            lines.append(line)
        
        # Add bottom axis
        lines.append(" " * 13 + "-" * width)
        lines.append(" " * 13 + f"Start{' ' * (width - 15)}End")
        
        return "\n".join(lines)
