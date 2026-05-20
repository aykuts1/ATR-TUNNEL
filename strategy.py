"""
strategy.py - Giris kosullarini kontrol eder.

Long Giris (3 kosul):
  1. Anlik fiyat dis ust bandin ustunde
  2. Asiri ust cizgiyi gecmemis
  3. Son lookback_saniye'de fiyat hic dis ust bandin ustunde bulunmamis

Short Giris: simetrik.
"""
from dataclasses import dataclass
from typing import Optional

from bands import Bands
from price_history import PriceHistory


@dataclass
class EntrySignal:
    side: str  # "LONG" veya "SHORT"
    price: float
    bands: Bands


def check_long_entry(
    symbol: str,
    price: float,
    bands: Bands,
    history: PriceHistory,
) -> bool:
    """
    Long giris kosullari.
    Tum kosullar saglandiginda True doner.
    Veri yetersizse False doner (guvenli taraf).

    Bu fonksiyon cagiranci, ANLIK fiyati history'ye HENUZ EKLEMEDEN
    once cagirmalidir. Boylece "son 300 sn'de fiyat dis bandi gecmemis"
    kontrolu su anki fiyati haric tutar.
    """
    # 1. Fiyat dis ust bandin ustunde mi?
    if price <= bands.dis_ust:
        return False

    # 2. Asiri ust cizgiyi gecmemis mi?
    if price >= bands.asiri_ust:
        return False

    # 3. Son lookback'de fiyat hic dis ust bandin ustunde bulunmamis mi?
    if not history.is_ready(symbol):
        # Yeterli gecmis yok, guvenli taraf: sinyal verme
        return False

    max_in_window = history.max_in_window(symbol)
    if max_in_window is None:
        return False
    # Pencere icindeki HER fiyat dis_ust'un altinda olmali.
    # (Su anki fiyat henuz eklenmedi.)
    if max_in_window > bands.dis_ust:
        return False

    return True


def check_short_entry(
    symbol: str,
    price: float,
    bands: Bands,
    history: PriceHistory,
) -> bool:
    """Short giris kosullari - simetrik."""
    if price >= bands.dis_alt:
        return False
    if price <= bands.asiri_alt:
        return False
    if not history.is_ready(symbol):
        return False
    min_in_window = history.min_in_window(symbol)
    if min_in_window is None:
        return False
    if min_in_window < bands.dis_alt:
        return False
    return True


def detect_entry(
    symbol: str,
    price: float,
    bands: Bands,
    history: PriceHistory,
) -> Optional[EntrySignal]:
    """
    Long ya da short sinyal varsa EntrySignal doner, yoksa None.
    Long ve short ayni anda olamaz (mantiksal).
    """
    if check_long_entry(symbol, price, bands, history):
        return EntrySignal(side="LONG", price=price, bands=bands)
    if check_short_entry(symbol, price, bands, history):
        return EntrySignal(side="SHORT", price=price, bands=bands)
    return None


def compute_be_line(side: str, dis_band: float, maker_oran: float) -> float:
    """
    BE cizgisi - komisyon karsilamasi.
    Long: dis_ust * (1 + maker_oran)
    Short: dis_alt * (1 - maker_oran)
    """
    if side == "LONG":
        return dis_band * (1 + maker_oran)
    else:
        return dis_band * (1 - maker_oran)
