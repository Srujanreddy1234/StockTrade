from backend.backtest.data_providers.base import DataProvider, InvalidCandleError
from backend.backtest.data_providers.yfinance_provider import YFinanceProvider
from backend.backtest.data_providers.groww_provider import GrowwProvider

__all__ = ["DataProvider", "InvalidCandleError", "YFinanceProvider", "GrowwProvider"]


def get_provider(name: str) -> DataProvider:
    """Factory used by the backtest engine's --data-provider option. Default
    development behavior stays yfinance until Groww's data is validated --
    see backend/backtest/engine.py.
    """
    if name == "groww":
        return GrowwProvider()
    if name == "yfinance":
        return YFinanceProvider()
    raise ValueError(f"Unknown data provider: {name}")
