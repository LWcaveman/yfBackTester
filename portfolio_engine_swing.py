"""
Backward Compatibility Alias for PortfolioBacktesterSwing
Delegates entirely to the unified PortfolioBacktester in portfolio_engine.py.
"""

from portfolio_engine import PortfolioBacktester as PortfolioBacktesterSwing

__all__ = ["PortfolioBacktesterSwing"]