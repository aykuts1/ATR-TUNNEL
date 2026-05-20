"""
bands.py - EMA, ATR ve 7 cizgi hesaplama.
Bybit'ten gelen mum verisini kullanarak bant degerlerini hesaplar.
"""
from dataclasses import dataclass
from typing import List, Tuple


@dataclass
class Bands:
    """Hesaplanmis bant degerleri."""
    ema: float
    atr: float
    ic_ust: float
    ic_alt: float
    dis_ust: float
    dis_alt: float
    asiri_ust: float
    asiri_alt: float


def _ema(values: List[float], period: int) -> float:
    """Klasik EMA. Son deger doner."""
    if len(values) < period:
        raise ValueError(f"EMA icin yeterli veri yok: {len(values)} < {period}")
    k = 2.0 / (period + 1.0)
    # Ilk EMA = ilk period'un SMA'si
    ema = sum(values[:period]) / period
    for v in values[period:]:
        ema = v * k + ema * (1 - k)
    return ema


def _atr(highs: List[float], lows: List[float], closes: List[float], period: int) -> float:
    """Klasik ATR (Wilder's smoothing). Son deger doner."""
    if len(highs) < period + 1:
        raise ValueError(f"ATR icin yeterli veri yok: {len(highs)} < {period + 1}")

    # True Range listesi
    trs = []
    for i in range(1, len(highs)):
        h = highs[i]
        l = lows[i]
        pc = closes[i - 1]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)

    # Ilk ATR = ilk period TR'nin ortalamasi
    atr = sum(trs[:period]) / period
    # Wilder smoothing
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def compute_bands(
    klines: List[List],
    ema_period: int,
    atr_period: int,
    ic_carpan: float,
    dis_carpan: float,
    asiri_carpan: float,
) -> Bands:
    """
    Bybit kline formati: [timestamp, open, high, low, close, volume, turnover]
    Bybit yeni mumu en bashta dondurur, eski mum sonda.
    Bu fonksiyon kronolojik sirayla isler (eski -> yeni).
    """
    if len(klines) < max(ema_period, atr_period) + 2:
        raise ValueError(
            f"Yeterli mum yok: {len(klines)} < {max(ema_period, atr_period) + 2}"
        )

    # Kronolojik sira: eski -> yeni
    # Son kapanmis mumu kullaniyoruz (acik olan mum hariç)
    # Cagiran taraf isterse acik mumu cikarir; biz tum listeyi kullaniriz
    closes = [float(k[4]) for k in klines]
    highs = [float(k[2]) for k in klines]
    lows = [float(k[3]) for k in klines]

    ema = _ema(closes, ema_period)
    atr = _atr(highs, lows, closes, atr_period)

    dis_ust = ema + atr * dis_carpan
    dis_alt = ema - atr * dis_carpan
    ic_ust = ema + atr * ic_carpan
    ic_alt = ema - atr * ic_carpan
    asiri_ust = dis_ust + atr * asiri_carpan
    asiri_alt = dis_alt - atr * asiri_carpan

    return Bands(
        ema=ema,
        atr=atr,
        ic_ust=ic_ust,
        ic_alt=ic_alt,
        dis_ust=dis_ust,
        dis_alt=dis_alt,
        asiri_ust=asiri_ust,
        asiri_alt=asiri_alt,
    )


def sort_klines_chronological(klines: List[List]) -> List[List]:
    """Bybit yanitini timestamp'e gore artan siralar (eski -> yeni)."""
    return sorted(klines, key=lambda k: int(k[0]))
