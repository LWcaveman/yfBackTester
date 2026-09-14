from abc import ABC, abstractmethod
import pandas as pd

class BaseStrategy(ABC):
    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Takes raw OHLCV and returns a DataFrame with signal metadata:
        setup_valid, entry_price, stop_loss, target_1r, target_2r
        """
        pass