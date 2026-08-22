from abc import ABC, abstractmethod


class DataSourceBase(ABC):
    @abstractmethod
    def get_financials(self, ticker: str) -> dict: ...

    @abstractmethod
    def get_news(self, ticker: str, days: int = 3) -> list[dict]: ...

    @abstractmethod
    def get_price(self, ticker: str) -> dict: ...

    @abstractmethod
    def get_peers(self, ticker: str) -> dict: ...
