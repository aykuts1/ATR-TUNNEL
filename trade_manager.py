"""
Trade Manager
- Trade objesi (tek bir işlemi temsil eder)
- SlotManager (hangi coinde ne açık, kim kime bağlı)
- TradeManager (açma, kapatma, stake hesabı, PnL hesabı)
"""
import math
import threading
import time
import logging

from utils import now_ts, safe_float, fmt_money

log = logging.getLogger("TradeManager")


# =========================================================================
# TRADE OBJESİ
# =========================================================================
class Trade:
    _id_counter = 0
    _id_lock = threading.Lock()

    def __init__(self, symbol, side, thread, entry_price, qty,
                 lose_line=None, winrate_line=None,
                 level_lines=None, current_level=None,
                 position_idx=None):
        with Trade._id_lock:
            Trade._id_counter += 1
            self.id = Trade._id_counter

        self.symbol = symbol
        self.side = side  # "LONG" / "SHORT"
        self.thread = thread  # "RED" / "BLUE" / "YELLOW"
        self.entry_price = float(entry_price)
        self.qty = float(qty)
        self.opened_ts = now_ts()

        self.current_level = current_level
        self.highest_level = current_level

        self.lose_line = lose_line
        self.winrate_line = winrate_line
        self.level_lines = dict(level_lines) if level_lines else {}

        self.position_idx = position_idx

        # Kapanış bilgileri
        self.closed = False
        self.close_price = None
        self.close_ts = None
        self.exit_name = None
        self.pnl_usdt = 0.0
        self.pnl_pct = 0.0

        # SARI chandelier alanları (sadece YELLOW thread için kullanılır)
        self.chandelier_distance = None
        self.chandelier_best_price = None
        self.chandelier_line = None

        # SARI yeniden giriş çizgisi (chandelier sonrası hafıza)
        self.reentry_line = None

        # MAVİ dinamik çıkış çizgisi alanları (sadece BLUE thread için)
        self.exit_line = None              # Şu anki çıkış çizgisi (fiyat cinsi)
        self.exit_line_updated = False     # %0.2 eşiğine ulaşıldı mı? (bir kez güncelleme kilidi)

        # Ek bilgiler (parent_red_id vs)
        self.extras = {}

    def duration_sec(self):
        end = self.close_ts if self.closed else now_ts()
        return max(0, end - self.opened_ts)


# =========================================================================
# SLOT MANAGER
# =========================================================================
class SlotManager:
    """
    Slot kuralları (kilitli):
    - Her coine en fazla 1 Kırmızı (yön farketmez).
    - Her Kırmızı'ya 1 Mavi + 1 Sarı.
    - External slot mantığı KALDIRILDI (madde 6).
    """
    def __init__(self):
        self.lock = threading.Lock()
        # (symbol, side, thread) -> Trade
        self.trades = {}
        # red_trade_id -> {"blue": Trade|None, "yellow": Trade|None}
        self.red_links = {}

    # ---- COIN KIRMIZI VARLIK KONTROLÜ ----
    def coin_has_red(self, symbol):
        """O coinde herhangi yönde açık Kırmızı var mı?"""
        with self.lock:
            for (s, side, thr), t in self.trades.items():
                if s == symbol and thr == "RED" and not t.closed:
                    return True
            return False

    # ---- AÇMA ÖNCESİ KONTROL ----
    def red_can_open(self, symbol):
        """Bu coinde yeni Kırmızı açılabilir mi?"""
        if self.coin_has_red(symbol):
            return (False, "COİNDE KIRMIZI VAR")
        return (True, None)

    def blue_can_open(self, red_id):
        """Bu Kırmızı'ya yeni Mavi açılabilir mi?"""
        with self.lock:
            link = self.red_links.get(red_id)
            if link and link.get("blue") and not link["blue"].closed:
                return (False, "BU KIRMIZIYA BAĞLI MAVİ ZATEN VAR")
            return (True, None)

    def yellow_can_open(self, red_id):
        with self.lock:
            link = self.red_links.get(red_id)
            if link and link.get("yellow") and not link["yellow"].closed:
                return (False, "BU KIRMIZIYA BAĞLI SARI ZATEN VAR")
            return (True, None)

    # ---- REGISTER ----
    def register_red(self, trade):
        with self.lock:
            self.trades[(trade.symbol, trade.side, "RED")] = trade
            self.red_links[trade.id] = {"blue": None, "yellow": None}

    def register_blue(self, trade, red_id):
        with self.lock:
            self.trades[(trade.symbol, trade.side, "BLUE")] = trade
            link = self.red_links.setdefault(red_id, {"blue": None, "yellow": None})
            link["blue"] = trade
            trade.extras["parent_red_id"] = red_id

    def register_yellow(self, trade, red_id):
        with self.lock:
            self.trades[(trade.symbol, trade.side, "YELLOW")] = trade
            link = self.red_links.setdefault(red_id, {"blue": None, "yellow": None})
            link["yellow"] = trade
            trade.extras["parent_red_id"] = red_id

    # ---- UNREGISTER ----
    def unregister(self, trade):
        with self.lock:
            key = (trade.symbol, trade.side, trade.thread)
            if key in self.trades and self.trades[key].id == trade.id:
                del self.trades[key]
            if trade.thread == "RED":
                self.red_links.pop(trade.id, None)
            else:
                # Mavi/Sarı kapanışında parent linkten temizle
                parent_red_id = trade.extras.get("parent_red_id")
                if parent_red_id is not None:
                    link = self.red_links.get(parent_red_id)
                    if link:
                        if trade.thread == "BLUE" and link.get("blue") and link["blue"].id == trade.id:
                            link["blue"] = None
                        if trade.thread == "YELLOW" and link.get("yellow") and link["yellow"].id == trade.id:
                            link["yellow"] = None

    # ---- READ HELPERS ----
    def get_red_link(self, red_id):
        with self.lock:
            return self.red_links.get(red_id)

    def get_red_for(self, symbol, side):
        """O coinde verilen yönde açık Kırmızı'yı O(1) döndür."""
        with self.lock:
            return self.trades.get((symbol, side, "RED"))

    def get_red_for_symbol(self, symbol):
        """O coinde hangi yönde olursa olsun açık Kırmızı."""
        with self.lock:
            for (s, side, thr), t in self.trades.items():
                if s == symbol and thr == "RED" and not t.closed:
                    return t
            return None

    def get_all_open(self):
        with self.lock:
            return [t for t in self.trades.values() if not t.closed]

    def get_open_by_thread(self, thread):
        with self.lock:
            return [t for (s, sd, thr), t in self.trades.items()
                    if thr == thread and not t.closed]

    def count_by_thread(self):
        """Açık işlem sayısı kırılımı."""
        counts = {"RED": 0, "BLUE": 0, "YELLOW": 0}
        with self.lock:
            for t in self.trades.values():
                if not t.closed:
                    counts[t.thread] = counts.get(t.thread, 0) + 1
        return counts


# =========================================================================
# TRADE MANAGER
# =========================================================================
class TradeManager:
    def __init__(self, config, data_manager, telegram_notifier):
        self.cfg = config
        self.dm = data_manager
        self.tg = telegram_notifier

        self.slots = SlotManager()

        # History
        self.closed_trades_history = []  # list of Trade
        self.flag_history = []  # list of dict
        self.errors_history = []  # list of dict

        # Sayaçlar
        self._insufficient_balance_count = 0
        self._slot_full_count = 0
        self._error_count = 0

        # Stake
        self.stake_usdt = 0.0
        self._stake_lock = threading.Lock()

        # İlk stake hesabı
        self.update_stake()

        # Rate limit koruması: ardışık order arası küçük bekleme
        self._order_lock = threading.Lock()
        self._last_order_ts = 0.0
        self._order_min_gap_sec = 0.1

    # ------------------------------------------------------------------
    # STAKE
    # ------------------------------------------------------------------
    def update_stake(self):
        bal = self.dm.get_balance()
        new_stake = bal * (self.cfg.stake_pct / 100.0)
        with self._stake_lock:
            self.stake_usdt = new_stake
        return new_stake

    def get_stake(self):
        with self._stake_lock:
            return self.stake_usdt

    # ------------------------------------------------------------------
    # YARDIMCILAR
    # ------------------------------------------------------------------
    def _position_idx(self, side):
        return 1 if side == "LONG" else 2

    def _order_side(self, side):
        return "Buy" if side == "LONG" else "Sell"

    def _close_side(self, side):
        return "Sell" if side == "LONG" else "Buy"

    def _calc_qty(self, symbol, entry_price):
        """Stake × leverage / entry → qtyStep'e taban yuvarlama."""
        info = self.dm.get_instrument_info(symbol)
        if not info:
            return 0.0
        stake = self.get_stake()
        raw = (stake * self.cfg.leverage) / entry_price
        step = info["qtyStep"]
        if step <= 0:
            return 0.0
        qty = math.floor(raw / step) * step
        # ondalık basamak hassasiyeti
        if step < 1:
            decimals = max(0, -int(math.floor(math.log10(step))))
        else:
            decimals = 0
        qty = round(qty, decimals + 4)
        if qty < info["minOrderQty"]:
            return 0.0
        return qty

    def _round_to_tick(self, price, tick_size, side, is_sl=True):
        """
        SL fiyatını tickSize'a yuvarla.
        SL "güvenli tarafa" yuvarlanır (entry'den uzağa, koruma genişlesin).
        - Long SL (entry altında): aşağı yuvarla.
        - Short SL (entry üstünde): yukarı yuvarla.
        """
        if tick_size <= 0:
            return price
        if is_sl:
            if side == "LONG":
                # SL entry'den aşağıda, daha aşağı yuvarla
                return math.floor(price / tick_size) * tick_size
            else:
                # SL entry'den yukarıda, daha yukarı yuvarla
                return math.ceil(price / tick_size) * tick_size
        # generic round
        return round(price / tick_size) * tick_size

    def _calc_hard_sl(self, symbol, side, entry_price):
        """Borsaya konacak hard SL fiyatı (tickSize'a yuvarlanmış)."""
        pct = self.cfg.hard_sl_pct / 100.0
        if side == "LONG":
            raw = entry_price * (1.0 - pct)
        else:
            raw = entry_price * (1.0 + pct)

        info = self.dm.get_instrument_info(symbol)
        tick = info["tickSize"] if info else 0.0
        if tick > 0:
            return self._round_to_tick(raw, tick, side, is_sl=True)
        return round(raw, 8)

    def _rate_limit_order(self):
        """Ardışık emirler arasına 100ms minimum gecikme."""
        with self._order_lock:
            gap = time.time() - self._last_order_ts
            if gap < self._order_min_gap_sec:
                time.sleep(self._order_min_gap_sec - gap)
            self._last_order_ts = time.time()

    # ------------------------------------------------------------------
    # AÇMA
    # ------------------------------------------------------------------
    def open_trade(self, symbol, side, thread, entry_price,
                   lose_line=None, winrate_line=None,
                   level_lines=None, current_level=None,
                   parent_red_trade=None):
        """
        Yeni işlem aç.
        Returns: Trade obj veya None.
        """
        # Slot kontrolü
        if thread == "RED":
            ok, msg = self.slots.red_can_open(symbol)
            if not ok:
                self._slot_full_count += 1
                self.tg.notify_slot_full(symbol, side, thread, msg)
                return None
        elif thread == "BLUE":
            if not parent_red_trade:
                return None
            ok, msg = self.slots.blue_can_open(parent_red_trade.id)
            if not ok:
                return None
        elif thread == "YELLOW":
            if not parent_red_trade:
                return None
            ok, msg = self.slots.yellow_can_open(parent_red_trade.id)
            if not ok:
                return None

        # Qty hesap
        qty = self._calc_qty(symbol, entry_price)
        if qty <= 0:
            self._insufficient_balance_count += 1
            self.tg.notify_insufficient_balance(symbol, side, thread, entry_price)
            return None

        # Hard SL
        hard_sl = self._calc_hard_sl(symbol, side, entry_price)

        # Bybit order
        try:
            self._rate_limit_order()
            self.dm.place_market_order(
                symbol=symbol,
                side=self._order_side(side),
                qty=qty,
                position_idx=self._position_idx(side),
                stop_loss=hard_sl,
            )
        except Exception as e:
            self._error_count += 1
            self.errors_history.append({
                "ts": now_ts(), "title": "Order açılamadı",
                "symbol": symbol, "module": "TradeManager", "detail": str(e),
            })
            self.tg.notify_error("Order açılamadı", symbol, "TradeManager", str(e))
            return None

        # Pozisyon doğrulama (1.5 sn bekle, gerçekten açıldı mı?)
        time.sleep(1.5)
        avg_price = self.dm.get_position_avg_price(symbol, self._position_idx(side))
        if avg_price is None:
            self._error_count += 1
            self.tg.notify_error(
                "Pozisyon doğrulanamadı (Bybit'te açılmamış olabilir)",
                symbol, "TradeManager",
                f"side={side} qty={qty} entry={entry_price}",
            )
            return None

        # Gerçek dolum fiyatı (Bybit'in fiili açılış fiyatı)
        real_entry = avg_price

        # Trade objesi
        trade = Trade(
            symbol=symbol, side=side, thread=thread,
            entry_price=real_entry, qty=qty,
            lose_line=lose_line, winrate_line=winrate_line,
            level_lines=level_lines, current_level=current_level,
            position_idx=self._position_idx(side),
        )

        # Register
        if thread == "RED":
            self.slots.register_red(trade)
        elif thread == "BLUE":
            self.slots.register_blue(trade, parent_red_trade.id)
        elif thread == "YELLOW":
            self.slots.register_yellow(trade, parent_red_trade.id)

        # Telegram bildirim
        self.tg.notify_trade_open(trade, hard_sl=hard_sl)
        log.info(f"İşlem açıldı: {thread} {side} {symbol} @ {real_entry} qty={qty}")

        return trade

    # ------------------------------------------------------------------
    # KAPATMA
    # ------------------------------------------------------------------
    def close_trade(self, trade, exit_name, close_price_hint=None):
        """
        Tek bir işlemi kapat. close_price_hint sadece "tahmini" bir değer,
        gerçek dolum fiyatı Bybit'ten alınır.
        """
        if trade.closed:
            return False

        try:
            self._rate_limit_order()
            self.dm.close_position_market(
                symbol=trade.symbol,
                side_to_close=self._close_side(trade.side),
                qty=trade.qty,
                position_idx=trade.position_idx,
            )
        except Exception as e:
            self._error_count += 1
            self.errors_history.append({
                "ts": now_ts(), "title": "Order kapatılamadı",
                "symbol": trade.symbol, "module": "TradeManager", "detail": str(e),
            })
            self.tg.notify_error("Order kapatılamadı", trade.symbol, "TradeManager", str(e))
            # Yine de trade'i kapalı işaretle ki sonsuz kapatma denenmesin
            close_price = close_price_hint if close_price_hint else trade.entry_price
            self._finalize_close(trade, exit_name, close_price)
            return False

        # 1 sn bekle, gerçek kapanış fiyatını çek (pozisyon kapandığı için artık görünmez,
        # closed PnL gerçek fiyatı yansıtır). Kısa süre sonra son fiyatı gerçek olarak alıyoruz.
        time.sleep(1.0)
        # Borsadan en son fiyat (gerçek dolum fiyatı yaklaşımı)
        actual_close = self.dm.get_last_price(trade.symbol)
        if actual_close is None:
            actual_close = close_price_hint if close_price_hint else trade.entry_price

        self._finalize_close(trade, exit_name, actual_close)
        return True

    def _finalize_close(self, trade, exit_name, close_price):
        trade.closed = True
        trade.close_price = float(close_price)
        trade.close_ts = now_ts()
        trade.exit_name = exit_name

        # PnL hesabı
        if trade.entry_price == 0:
            pnl_raw = 0.0
        elif trade.side == "LONG":
            pnl_raw = (trade.close_price - trade.entry_price) / trade.entry_price
        else:
            pnl_raw = (trade.entry_price - trade.close_price) / trade.entry_price

        # Stake o işlem açıldığında neyse onu yansıtmak için açılış anındaki stake olmalı.
        # Şu an global stake_usdt kullanıyoruz — yeterince yakın.
        stake = self.get_stake()
        trade.pnl_usdt = stake * self.cfg.leverage * pnl_raw
        trade.pnl_pct = pnl_raw * self.cfg.leverage * 100.0

        # History
        self.closed_trades_history.append(trade)
        # Unregister slot
        self.slots.unregister(trade)

        # Telegram
        self.tg.notify_trade_close(trade)
        log.info(f"İşlem kapandı: {trade.thread} {trade.side} {trade.symbol} "
                 f"@ {trade.close_price} PnL={fmt_money(trade.pnl_usdt)} "
                 f"({trade.pnl_pct:+.2f}%) — {exit_name}")

    # ------------------------------------------------------------------
    # KIRMIZI + BAĞIMLI KAPATMA
    # ------------------------------------------------------------------
    def close_red_and_dependents(self, red_trade, exit_name, close_price_hint=None):
        """Kırmızı'yı kapatırken bağlı Mavi+Sarı varsa önce onları kapatır."""
        if red_trade.thread != "RED":
            log.warning(f"close_red_and_dependents Kırmızı olmayan trade için çağrıldı: {red_trade.thread}")
            return

        link = self.slots.get_red_link(red_trade.id)
        if link:
            blue = link.get("blue")
            yellow = link.get("yellow")
            if blue and not blue.closed:
                self.close_trade(blue, "KIRMIZI KAPANDI", close_price_hint)
            if yellow and not yellow.closed:
                self.close_trade(yellow, "KIRMIZI KAPANDI", close_price_hint)

        self.close_trade(red_trade, exit_name, close_price_hint)

    # ------------------------------------------------------------------
    # FLAG HISTORY (raporlar için)
    # ------------------------------------------------------------------
    def log_flag_event(self, symbol, thread, side, event):
        """event: OPENED, DELETED, CONVERTED"""
        self.flag_history.append({
            "ts": now_ts(),
            "symbol": symbol,
            "thread": thread,
            "side": side,
            "event": event,
        })

    # ------------------------------------------------------------------
    # HISTORY OKUMA (raporlar için)
    # ------------------------------------------------------------------
    def get_closed_trades_window(self, start_ts, end_ts):
        return [t for t in self.closed_trades_history
                if t.close_ts is not None and start_ts <= t.close_ts <= end_ts]

    def get_flag_events_window(self, start_ts, end_ts):
        return [e for e in self.flag_history if start_ts <= e["ts"] <= end_ts]

    def get_errors_window(self, start_ts, end_ts):
        return [e for e in self.errors_history if start_ts <= e["ts"] <= end_ts]

    def get_all_closed_trades(self):
        return list(self.closed_trades_history)

    def get_counters(self):
        return {
            "insufficient_balance": self._insufficient_balance_count,
            "slot_full": self._slot_full_count,
            "error": self._error_count,
        }
