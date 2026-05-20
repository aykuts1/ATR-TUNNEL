"""
price_history.py - Her coin icin son N saniyenin fiyat gecmisini tutar.
Giris kontrolu icin: son 300 sn'de fiyat dis bandin ustunde/altinda bulundu mu?
"""
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, Tuple
import time


@dataclass
class PricePoint:
    timestamp: float  # unix saniye
    price: float


class PriceHistory:
    """
    Her sembol icin (timestamp, price) ikilileri tutar.
    Otomatik olarak lookback_saniye'den eski olanlari atar.
    """

    def __init__(self, lookback_saniye: int):
        self.lookback = lookback_saniye
        self._data: Dict[str, Deque[PricePoint]] = {}

    def add(self, symbol: str, price: float, now: float = None) -> None:
        if now is None:
            now = time.time()
        if symbol not in self._data:
            self._data[symbol] = deque()
        self._data[symbol].append(PricePoint(now, price))
        self._prune(symbol, now)

    def _prune(self, symbol: str, now: float) -> None:
        dq = self._data.get(symbol)
        if not dq:
            return
        cutoff = now - self.lookback
        while dq and dq[0].timestamp < cutoff:
            dq.popleft()

    def max_in_window(self, symbol: str, now: float = None) -> float:
        """Pencere icindeki en yuksek fiyat. Veri yoksa None."""
        if now is None:
            now = time.time()
        self._prune(symbol, now)
        dq = self._data.get(symbol)
        if not dq:
            return None
        return max(p.price for p in dq)

    def min_in_window(self, symbol: str, now: float = None) -> float:
        """Pencere icindeki en dusuk fiyat. Veri yoksa None."""
        if now is None:
            now = time.time()
        self._prune(symbol, now)
        dq = self._data.get(symbol)
        if not dq:
            return None
        return min(p.price for p in dq)

    def coverage_seconds(self, symbol: str, now: float = None) -> float:
        """Bu sembol icin kac saniyelik veri var."""
        if now is None:
            now = time.time()
        self._prune(symbol, now)
        dq = self._data.get(symbol)
        if not dq or len(dq) < 2:
            return 0.0
        return now - dq[0].timestamp

    def is_ready(self, symbol: str, now: float = None) -> bool:
        """
        Lookback penceresini doldurdu mu? Giris kararindan once kontrol edilir.

        Pratikte bot her 5sn'de bir veri ekledigi icin pencere hicbir zaman tam
        olarak `lookback_saniye` kadar olmaz, surekli `lookback - tarama_periodu`
        civarinda dans eder. Bu yuzden %95 doluluk yeterli kabul edilir.
        """
        return self.coverage_seconds(symbol, now) >= self.lookback * 0.95
