"""
position.py - Pozisyon state'i, CE seviyeleri ve cikis kontrolu.

Seviye 1: Giris  (CE pasif)
Seviye 2: CE Stage 1  (kar >= stage1_tetik * ATR -> stage1_takip * ATR geri)
Seviye 3: CE Stage 2  (kar >= stage2_tetik * ATR -> stage2_takip * ATR geri)
Seviye 4: Winrate     (kar >= winrate_tetik * ATR -> winrate_takip * ATR geri)

CE asla geri cekilmez. Sadece fiyat ilerledikce ilerler.

Cikis Tipleri:
  - Lose Exit     : Dis banda girdi veya BE cizgisine carpti
  - CE1 Exit      : CE Stage 1 seviyesine carpti
  - CE2 Exit      : CE Stage 2 seviyesine carpti
  - Winrate Exit  : Winrate CE seviyesine carpti
  - Stoploss Exit : Borsa SL tetiklendi (disardan tespit edilir)
"""
from dataclasses import dataclass, field
from typing import Optional
import time


# Seviye sabitleri
LEVEL_ENTRY = 1
LEVEL_CE1 = 2
LEVEL_CE2 = 3
LEVEL_WINRATE = 4

LEVEL_LABELS = {
    LEVEL_ENTRY: "Giris",
    LEVEL_CE1: "CE Stage 1",
    LEVEL_CE2: "CE Stage 2",
    LEVEL_WINRATE: "Winrate",
}


@dataclass
class Position:
    """Bot tarafindan acilan ve takip edilen pozisyon."""
    symbol: str
    side: str  # "LONG" / "SHORT"
    entry_price: float
    qty: float
    stake: float       # USDT cinsinden teminat
    notional: float    # giris_fiyati * qty (pozisyon buyuklugu USDT)
    leverage: int
    atr_at_entry: float
    be_line: float
    stop_loss_price: float
    open_time: float = field(default_factory=time.time)

    # State
    level: int = LEVEL_ENTRY
    best_price: float = 0.0   # long icin max, short icin min gorulen fiyat
    ce_price: Optional[float] = None  # mevcut CE seviyesi

    def __post_init__(self):
        if self.best_price == 0.0:
            self.best_price = self.entry_price

    def update_best(self, price: float) -> None:
        """En iyi fiyati guncelle (long: max, short: min)."""
        if self.side == "LONG":
            if price > self.best_price:
                self.best_price = price
        else:
            if price < self.best_price:
                self.best_price = price

    def profit_in_atr(self, price: float) -> float:
        """Karin ATR cinsinden buyuklugu (negatif olabilir)."""
        if self.atr_at_entry <= 0:
            return 0.0
        if self.side == "LONG":
            diff = price - self.entry_price
        else:
            diff = self.entry_price - price
        return diff / self.atr_at_entry

    def profit_pct(self, price: float) -> float:
        """Kar/zarar yuzdesi (kaldıraçsız)."""
        if self.side == "LONG":
            return (price - self.entry_price) / self.entry_price * 100.0
        else:
            return (self.entry_price - price) / self.entry_price * 100.0

    def profit_usdt(self, price: float) -> float:
        """Kar/zarar USDT cinsinden (kaldıraçlı)."""
        if self.side == "LONG":
            return (price - self.entry_price) * self.qty
        else:
            return (self.entry_price - price) * self.qty


def compute_ce_level(
    pos: Position,
    trail_atr_multiple: float,
) -> float:
    """
    CE seviyesi = best_price -/+ trail_atr_multiple * ATR
    Long: best_price - trail
    Short: best_price + trail
    """
    trail = trail_atr_multiple * pos.atr_at_entry
    if pos.side == "LONG":
        return pos.best_price - trail
    else:
        return pos.best_price + trail


def update_level_and_ce(
    pos: Position,
    price: float,
    stage1_tetik: float, stage1_takip: float,
    stage2_tetik: float, stage2_takip: float,
    winrate_tetik: float, winrate_takip: float,
) -> Optional[int]:
    """
    Seviye yukseltmesini kontrol eder ve CE'yi guceller.
    Seviye yukselirse yeni seviye numarasini doner; yukselmediyse None.
    CE asla geri cekilmez (yeni CE eskisinden daha kotuyse eski tutulur).
    """
    pos.update_best(price)
    profit_atr = pos.profit_in_atr(pos.best_price)

    new_level = pos.level
    if profit_atr >= winrate_tetik and pos.level < LEVEL_WINRATE:
        new_level = LEVEL_WINRATE
    elif profit_atr >= stage2_tetik and pos.level < LEVEL_CE2:
        new_level = LEVEL_CE2
    elif profit_atr >= stage1_tetik and pos.level < LEVEL_CE1:
        new_level = LEVEL_CE1

    # Aktif takip carpani
    if new_level >= LEVEL_WINRATE:
        trail = winrate_takip
    elif new_level >= LEVEL_CE2:
        trail = stage2_takip
    elif new_level >= LEVEL_CE1:
        trail = stage1_takip
    else:
        trail = None

    # CE guncelleme
    if trail is not None:
        candidate = compute_ce_level(pos, trail)
        if pos.ce_price is None:
            pos.ce_price = candidate
        else:
            # CE geri cekilmez
            if pos.side == "LONG":
                pos.ce_price = max(pos.ce_price, candidate)
            else:
                pos.ce_price = min(pos.ce_price, candidate)

    if new_level != pos.level:
        pos.level = new_level
        return new_level
    return None


def check_exit(pos: Position, price: float, dis_band_now: float) -> Optional[str]:
    """
    Cikis kontrolu. Aktif olan tum cikis tetikleyicilerini sirayla kontrol eder.
    Tetiklenen varsa cikis tipini doner (string), yoksa None.

    Oncelik sirasi:
      1. CE (varsa)         -> CE1/CE2/Winrate Exit
      2. BE cizgisi         -> Lose Exit
      3. Dis bandin icine giris -> Lose Exit
      (Stoploss Exit disardan tespit edilir: borsa pozisyonu kapatti mi?)

    dis_band_now: o anki dis_ust (long icin) veya dis_alt (short icin)
    """
    # 1. CE (varsa)
    if pos.ce_price is not None:
        if pos.side == "LONG" and price <= pos.ce_price:
            return _ce_exit_name(pos.level)
        if pos.side == "SHORT" and price >= pos.ce_price:
            return _ce_exit_name(pos.level)

    # 2. BE cizgisi
    if pos.side == "LONG":
        # Long icin BE, dis bandin biraz USTUNDE. Fiyat oraya inerse cikilir.
        if price <= pos.be_line:
            return "Lose Exit"
    else:
        if price >= pos.be_line:
            return "Lose Exit"

    # 3. Dis bandin icine giris (BE cizgisini gecmemisken)
    if pos.side == "LONG":
        if price < dis_band_now:
            return "Lose Exit"
    else:
        if price > dis_band_now:
            return "Lose Exit"

    return None


def _ce_exit_name(level: int) -> str:
    if level == LEVEL_WINRATE:
        return "Winrate Exit"
    if level == LEVEL_CE2:
        return "CE2 Exit"
    if level == LEVEL_CE1:
        return "CE1 Exit"
    return "Lose Exit"


def next_level_target_price(
    pos: Position,
    stage1_tetik: float,
    stage2_tetik: float,
    winrate_tetik: float,
) -> Optional[float]:
    """Bir sonraki seviyenin hedef fiyati. Telegram raporlarinda gosterilir."""
    atr = pos.atr_at_entry
    if pos.level == LEVEL_ENTRY:
        target_atr = stage1_tetik
    elif pos.level == LEVEL_CE1:
        target_atr = stage2_tetik
    elif pos.level == LEVEL_CE2:
        target_atr = winrate_tetik
    else:
        return None

    if pos.side == "LONG":
        return pos.entry_price + target_atr * atr
    else:
        return pos.entry_price - target_atr * atr
