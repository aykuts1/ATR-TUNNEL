"""
order.py - Emir gonderme sistemi.

Giris emri: anlik fiyat - 1 tick (long) / + 1 tick (short)
            post-only, 3 sn bekle, dolmadiysa iptal -> yeni guncel fiyatla emir
            max 50 deneme.

Cikis emri: anlik fiyat + 1 tick (long cikis = Sell) / - 1 tick (short cikis = Buy)
            post-only, 3 sn bekle, dolmadiysa iptal -> yeni guncel fiyatla emir
            max 50 deneme.

Market emir KESINLIKLE yok.
"""
import math
import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class FillResult:
    filled: bool
    avg_price: float = 0.0
    qty: float = 0.0
    attempts: int = 0
    last_order_id: Optional[str] = None
    last_price: float = 0.0


def round_step(value: float, step: float) -> float:
    """Bybit kurali: tickSize/qtyStep'in katlari, floor."""
    if step <= 0:
        return value
    return math.floor(value / step) * step


def fmt_price(price: float, tick: float) -> str:
    """Tick'e gore yuvarlanmis fiyat stringi."""
    p = round_step(price, tick)
    # Tick'in ondalik basamak sayisina gore string
    decimals = max(0, -int(math.floor(math.log10(tick))) if tick < 1 else 0)
    return f"{p:.{decimals}f}"


def fmt_qty(qty: float, step: float) -> str:
    q = round_step(qty, step)
    decimals = max(0, -int(math.floor(math.log10(step))) if step < 1 else 0)
    return f"{q:.{decimals}f}"


def calc_qty(notional_usdt: float, leverage: int, price: float, qty_step: float, min_qty: float):
    """notional = stake * leverage. Miktari coin cinsinden hesapla."""
    if price <= 0:
        return 0.0
    raw_qty = notional_usdt / price
    rounded = round_step(raw_qty, qty_step)
    if rounded < min_qty:
        return 0.0
    return rounded


# --- Limit emir tekrarlama dongusu --------------------------------------


def submit_with_retries(
    bybit_client,
    symbol: str,
    bybit_side: str,   # "Buy" / "Sell"
    qty: float,
    is_entry: bool,
    instrument: dict,
    max_attempts: int,
    wait_seconds: int,
    reduce_only: bool = False,
    on_attempt_fail=None,  # opsiyonel callback(attempt_no, reason)
) -> FillResult:
    """
    Anlik fiyatin 1 tick alti/ustune post-only limit emir.
    `wait_seconds` bekler. Dolmadiysa iptal eder, yeni fiyatla tekrar dener.
    `max_attempts` kez tekrarlar. Dolduginda FillResult.filled = True.
    """
    tick = instrument["tick_size"]
    qty_step = instrument["qty_step"]
    qty_str = fmt_qty(qty, qty_step)

    result = FillResult(filled=False)

    for attempt in range(1, max_attempts + 1):
        result.attempts = attempt
        try:
            last_price = bybit_client.fetch_last_price(symbol)
        except Exception as e:
            if on_attempt_fail:
                on_attempt_fail(attempt, f"fetch_last_price hatasi: {e}")
            time.sleep(1)
            continue

        # 1 tick uzaklik (PostOnly garantisi icin)
        if bybit_side == "Buy":
            price = last_price - tick
        else:
            price = last_price + tick
        price_str = fmt_price(price, tick)
        result.last_price = float(price_str)

        # Emri gonder
        try:
            r = bybit_client.place_limit_post_only(
                symbol=symbol,
                side=bybit_side,
                qty=qty_str,
                price=price_str,
                reduce_only=reduce_only,
            )
            order_id = r.get("orderId")
            result.last_order_id = order_id
        except Exception as e:
            # PostOnly reddi olabilir veya baska bir hata
            if on_attempt_fail:
                on_attempt_fail(attempt, f"place_order hatasi: {e}")
            time.sleep(1)
            continue

        # Bekle
        time.sleep(wait_seconds)

        # Durumu kontrol et
        try:
            order = bybit_client.get_order(symbol, order_id)
        except Exception:
            order = None

        if order is not None:
            status = (order.get("orderStatus") or "").lower()
            cum_qty = float(order.get("cumExecQty") or 0)
            if status in ("filled", "partiallyfilledcanceled") and cum_qty > 0:
                avg = float(order.get("avgPrice") or price_str)
                result.filled = True
                result.avg_price = avg
                result.qty = cum_qty
                return result

        # Dolmadi -> iptal et ve tekrar dene
        if result.last_order_id:
            bybit_client.cancel_order(symbol, result.last_order_id)

        if on_attempt_fail:
            on_attempt_fail(attempt, "dolmadi")

    return result
