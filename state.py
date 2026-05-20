"""
state.py - Acik pozisyonlari hafizada tutar.
Threadsafe (giris ve cikis dongusu farkli thread'lerde).
"""
import threading
from typing import Dict, List, Optional

from position import Position


class StateManager:
    """Acik pozisyonlari thread-safe tutan basit container."""

    def __init__(self):
        self._lock = threading.RLock()
        self._positions: Dict[str, Position] = {}
        self._external_symbols: set = set()  # bot baslarken bulunan ve kontrol almadigi pozisyonlar
        # Sayaclar (Z raporlari icin)
        self._counters: Dict[str, int] = {}
        # Kapanmis isemler (Z raporlari icin)
        self._closed_log: List[dict] = []

    # --- Pozisyon yonetimi -------------------------------------------------
    def add_position(self, pos: Position) -> None:
        with self._lock:
            self._positions[pos.symbol] = pos

    def remove_position(self, symbol: str) -> Optional[Position]:
        with self._lock:
            return self._positions.pop(symbol, None)

    def get(self, symbol: str) -> Optional[Position]:
        with self._lock:
            return self._positions.get(symbol)

    def has(self, symbol: str) -> bool:
        with self._lock:
            return symbol in self._positions or symbol in self._external_symbols

    def open_symbols(self) -> List[str]:
        with self._lock:
            return list(self._positions.keys())

    def all_open(self) -> List[Position]:
        with self._lock:
            return list(self._positions.values())

    def total_slots_used(self) -> int:
        """Hem bot pozisyonlari hem disardan tespit edilen pozisyonlar."""
        with self._lock:
            return len(self._positions) + len(self._external_symbols)

    # --- External pozisyonlar (bot restart sonrasi) -----------------------
    def mark_external(self, symbol: str) -> None:
        with self._lock:
            self._external_symbols.add(symbol)

    def unmark_external(self, symbol: str) -> None:
        with self._lock:
            self._external_symbols.discard(symbol)

    def external_symbols(self) -> List[str]:
        with self._lock:
            return list(self._external_symbols)

    # --- Sayaclar (Z raporlari) -------------------------------------------
    def incr(self, key: str, value: int = 1) -> None:
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + value

    def reset_counter(self, key: str) -> None:
        with self._lock:
            self._counters[key] = 0

    def snapshot_counters(self) -> Dict[str, int]:
        with self._lock:
            return dict(self._counters)

    # --- Kapanan isemler ---------------------------------------------------
    def log_closed(self, record: dict) -> None:
        with self._lock:
            self._closed_log.append(record)

    def closed_since(self, ts: float) -> List[dict]:
        with self._lock:
            return [r for r in self._closed_log if r.get("close_time", 0) >= ts]

    def all_closed(self) -> List[dict]:
        with self._lock:
            return list(self._closed_log)
