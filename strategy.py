"""
Strateji: Sinyal üretimi ve filtre kontrolü.

Filtreler:
1. RSI crossover (dinamik eşik)
2. 48 mum öncesi + 0.5 ATR mesafe (trend yön filtresi)
3. ATR oranı ≥ 0.7 (volatilite filtresi)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

import config
import indicators


@dataclass
class SignalResult:
    """Tarama sonucu: sinyal ya da hangi filtreye takıldığı bilgisi."""
    symbol: str
    has_signal: bool = False
    side: Optional[str] = None
    crossover_happened: bool = False
    crossover_side: Optional[str] = None
    rejection_reason: Optional[str] = None

    # Debug / bilgi için
    last_close: float = 0.0
    rsi_value: float = 0.0
    rsi_long_th: float = 0.0
    rsi_short_th: float = 0.0
    atr_value: float = 0.0
    atr_ratio_value: float = 0.0
    trend_ref_price: float = 0.0     # 48 mum önceki kapanış


def evaluate_symbol(
    df_30m: pd.DataFrame,
    symbol: str,
) -> SignalResult:
    """
    Sembol için sinyal değerlendir.

    df_30m: 30 dakikalık kapanan mumlar (en eskiden en yeniye)
    """
    result = SignalResult(symbol=symbol)

    # === Yeterli veri kontrolü ===
    min_30m = max(
        config.RSI_LOOKBACK + config.RSI_PERIOD,
        config.ATR_LOOKBACK + config.ATR_PERIOD,
        config.TREND_LOOKBACK_BARS + 5,
    ) + 5
    if len(df_30m) < min_30m:
        result.rejection_reason = "Yetersiz veri"
        return result

    close_30m = df_30m["close"]
    high_30m = df_30m["high"]
    low_30m = df_30m["low"]

    # === Göstergeler ===
    rsi_series = indicators.rsi(close_30m, config.RSI_PERIOD)
    atr_series = indicators.atr(high_30m, low_30m, close_30m, config.ATR_PERIOD)

    last_close = float(close_30m.iloc[-1])
    last_rsi = float(rsi_series.iloc[-1])
    last_atr = float(atr_series.iloc[-1])

    if pd.isna(last_rsi) or pd.isna(last_atr):
        result.rejection_reason = "Gösterge NaN"
        return result

    # Dinamik RSI eşikleri
    long_th, short_th = indicators.dynamic_rsi_thresholds(
        rsi_series, config.RSI_LOOKBACK, config.RSI_EXTREME_COUNT
    )

    # ATR oranı
    atr_r = indicators.atr_ratio(atr_series, config.ATR_LOOKBACK)

    # 48 mum öncesi kapanış (trend referansı)
    trend_ref = float(close_30m.iloc[-config.TREND_LOOKBACK_BARS - 1])

    # Result'a doldur
    result.last_close = last_close
    result.rsi_value = last_rsi
    result.rsi_long_th = long_th
    result.rsi_short_th = short_th
    result.atr_value = last_atr
    result.atr_ratio_value = atr_r
    result.trend_ref_price = trend_ref

    # === RSI Crossover Kontrolü ===
    long_cross = indicators.rsi_cross_up(rsi_series, long_th)
    short_cross = indicators.rsi_cross_down(rsi_series, short_th)

    if not long_cross and not short_cross:
        result.rejection_reason = "RSI crossover yok"
        return result

    # Trend mesafesi (0.5 ATR)
    distance = config.TREND_ATR_DISTANCE * last_atr

    # === LONG değerlendirme ===
    if long_cross:
        result.crossover_happened = True
        result.crossover_side = "long"

        # Fiyat 48 mum öncesinin 0.5 ATR üstünde olmalı
        if last_close < trend_ref + distance:
            result.rejection_reason = (
                f"24H trend yukarı değil "
                f"(fiyat {last_close:.6f} < ref {trend_ref:.6f} + {distance:.6f})"
            )
            return result

        if atr_r < config.ATR_RATIO_MIN:
            result.rejection_reason = (
                f"ATR oranı düşük ({atr_r:.2f} < {config.ATR_RATIO_MIN})"
            )
            return result

        result.has_signal = True
        result.side = "long"
        return result

    # === SHORT değerlendirme ===
    if short_cross:
        result.crossover_happened = True
        result.crossover_side = "short"

        # Fiyat 48 mum öncesinin 0.5 ATR altında olmalı
        if last_close > trend_ref - distance:
            result.rejection_reason = (
                f"24H trend aşağı değil "
                f"(fiyat {last_close:.6f} > ref {trend_ref:.6f} - {distance:.6f})"
            )
            return result

        if atr_r < config.ATR_RATIO_MIN:
            result.rejection_reason = (
                f"ATR oranı düşük ({atr_r:.2f} < {config.ATR_RATIO_MIN})"
            )
            return result

        result.has_signal = True
        result.side = "short"
        return result

    return result


def compute_entry_atr(df_30m: pd.DataFrame) -> float:
    """Giriş anındaki ATR değeri (CE ve BE seviyelerinin hesabı için)."""
    atr_series = indicators.atr(
        df_30m["high"], df_30m["low"], df_30m["close"], config.ATR_PERIOD
    )
    val = atr_series.iloc[-1]
    return float(val) if not pd.isna(val) else 0.0


def compute_rsi_and_thresholds(df_30m: pd.DataFrame):
    """
    Anlık RSI değerini ve dinamik eşikleri döndür.
    Returns: (current_rsi, long_threshold, short_threshold) veya None
    """
    if len(df_30m) < config.RSI_LOOKBACK + config.RSI_PERIOD + 5:
        return None
    rsi_series = indicators.rsi(df_30m["close"], config.RSI_PERIOD)
    current_rsi = float(rsi_series.iloc[-1])
    if pd.isna(current_rsi):
        return None
    long_th, short_th = indicators.dynamic_rsi_thresholds(
        rsi_series, config.RSI_LOOKBACK, config.RSI_EXTREME_COUNT
    )
    return current_rsi, long_th, short_th
