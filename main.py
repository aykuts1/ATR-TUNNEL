"""
main.py - Ana giris noktasi.

Mimari:
  - Bir baslatma thread'i (ana thread): kurulum
  - Entry thread: 5 saniyede bir tum coinleri tara, sinyal varsa islem ac
  - Exit thread:  5 saniyede bir acik pozisyonlari kontrol et, cikis kosulu varsa kapat
  - Report thread: 10dk, saatlik, 12sa, 24sa raporlari

Her seyi config.json kontrol eder.
"""
import logging
import os
import signal as sig
import sys
import threading
import time
from datetime import datetime, timedelta
from typing import Dict, List

from config import Config
from bybit import BybitClient
from bands import compute_bands, sort_klines_chronological
from price_history import PriceHistory
from strategy import detect_entry, compute_be_line, EntrySignal
from position import (
    Position, update_level_and_ce, check_exit,
    next_level_target_price,
    LEVEL_ENTRY, LEVEL_CE1, LEVEL_CE2, LEVEL_WINRATE, LEVEL_LABELS,
)
from state import StateManager
from order import submit_with_retries, calc_qty, fmt_price, fmt_qty, FillResult
from notifier import TelegramNotifier
from reports import format_open_positions, format_skipped, build_period_report


# --- Logging ---------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(threadName)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("bot")


# --- Shutdown event --------------------------------------------------------
SHUTDOWN = threading.Event()


def _on_signal(signum, frame):
    log.info(f"Sinyal alindi: {signum}, kapaniyor...")
    SHUTDOWN.set()


# --- Bot wrapper -----------------------------------------------------------
class Bot:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.client = BybitClient(cfg.bybit_api_key, cfg.bybit_api_secret, testnet=False)
        self.tg = TelegramNotifier(cfg.telegram_token, cfg.telegram_chat_id)
        self.state = StateManager()
        self.history = PriceHistory(cfg.lookback_saniye)
        self._instruments: Dict[str, dict] = {}

        # Baslangic bakiye ve stake
        self.start_balance = 0.0
        self.stake_usdt = 0.0          # sabit, bot yeniden baslana kadar
        self.balance_at_start = 0.0    # rapor referansi

        # Raporlama icin son rapor timestamp'leri
        self._last_report_10m = 0.0
        self._last_report_hour = 0.0
        self._last_report_12h_date = None
        self._last_report_24h_date = None

    # ---------- Baslatma --------------------------------------------------
    def setup(self):
        log.info("Bot kuruluyor...")
        # 1) Bakiye
        self.start_balance = self.client.fetch_balance_usdt()
        self.balance_at_start = self.start_balance
        self.stake_usdt = self.start_balance * (self.cfg.stake_yuzde / 100.0)
        log.info(f"Bakiye: {self.start_balance:.2f} USDT | Stake: {self.stake_usdt:.2f} USDT")

        # 2) Instrument bilgileri ve kaldirac destekleyenler
        unsupported: List[dict] = []
        for sym in self.cfg.coinler:
            try:
                info = self.client.get_instrument_info(sym)
                self._instruments[sym] = info
                if info["max_leverage"] < self.cfg.kaldirac:
                    unsupported.append({"symbol": sym, "max": info["max_leverage"]})
                else:
                    # Isolated + kaldirac
                    try:
                        self.client.switch_isolated(sym, self.cfg.kaldirac)
                    except Exception as e:
                        log.warning(f"{sym} isolated mod ayarlanamadi: {e}")
                    try:
                        self.client.set_leverage(sym, self.cfg.kaldirac)
                    except Exception as e:
                        log.warning(f"{sym} kaldirac ayarlanamadi: {e}")
            except Exception as e:
                log.error(f"{sym} instrument bilgisi alinamadi: {e}")

        # 3) Mevcut acik pozisyonlar (bot kontrol almaz, sadece slot sayar)
        existing_symbols: List[str] = []
        try:
            positions = self.client.fetch_positions()
            for p in positions:
                sym = p.get("symbol")
                if sym:
                    self.state.mark_external(sym)
                    existing_symbols.append(sym)
        except Exception as e:
            log.warning(f"Mevcut pozisyonlar okunamadi: {e}")

        # 4) Telegram'a bot baslatildi mesaji
        try:
            self.tg.bot_started(
                balance=self.start_balance,
                stake=self.stake_usdt,
                leverage=self.cfg.kaldirac,
                max_positions=self.cfg.max_pozisyon,
                coin_count=len(self.cfg.coinler),
                timeframe_min=self.cfg.timeframe_dakika,
                stoploss_pct=self.cfg.stoploss_yuzde,
                existing_positions=existing_symbols,
                unsupported_leverage=unsupported,
                ema_period=self.cfg.ema_period,
                atr_period=self.cfg.atr_period,
                ic_carpan=self.cfg.ic_carpan,
                dis_carpan=self.cfg.dis_carpan,
                asiri_carpan=self.cfg.asiri_carpan,
                lookback_sec=self.cfg.lookback_saniye,
                scan_sec=self.cfg.tarama_saniye,
                stage1_t=self.cfg.stage1_tetik, stage1_k=self.cfg.stage1_takip,
                stage2_t=self.cfg.stage2_tetik, stage2_k=self.cfg.stage2_takip,
                winrate_t=self.cfg.winrate_tetik, winrate_k=self.cfg.winrate_takip,
                entry_attempts=self.cfg.giris_deneme,
                wait_sec=self.cfg.deneme_bekleme_sn,
            )
        except Exception as e:
            log.error(f"Telegram baslatma mesaji gonderilemedi: {e}")

    # ---------- Entry loop ------------------------------------------------
    def entry_loop(self):
        log.info("Entry thread basladi.")
        while not SHUTDOWN.is_set():
            t0 = time.time()
            try:
                self._scan_entries()
            except Exception as e:
                log.exception(f"Entry tarama hatasi: {e}")
                try:
                    self.tg.error(f"Entry tarama: {e}")
                except Exception:
                    pass
            # Tarama suresi kadar bekle
            elapsed = time.time() - t0
            sleep_for = max(0.5, self.cfg.tarama_saniye - elapsed)
            SHUTDOWN.wait(sleep_for)
        log.info("Entry thread durdu.")

    def _scan_entries(self):
        max_pos = self.cfg.max_pozisyon

        # External pozisyonlar hala acik mi kontrol et.
        # Kapanmissa (SL/TP/elle) slot serbest kalsin.
        for ext_sym in self.state.external_symbols():
            try:
                positions = self.client.fetch_positions()
                still_open = any(
                    p.get("symbol") == ext_sym and float(p.get("size") or 0) > 0
                    for p in positions
                )
                if not still_open:
                    self.state.unmark_external(ext_sym)
                    log.info(f"{ext_sym} external pozisyon kapanmis, slot serbest.")
            except Exception:
                pass  # API hatasi olursa bir sonraki turde tekrar dener

        for sym in self.cfg.coinler:
            if SHUTDOWN.is_set():
                break

            # External (bot kontrolunde olmayan) veya bot pozisyonu var mi?
            if self.state.has(sym):
                continue

            # Slot dolu mu?
            if self.state.total_slots_used() >= max_pos:
                # Tek bir uyari mesaji yeterli; bunu her tarama icin yollamiyoruz
                continue

            try:
                price = self.client.fetch_last_price(sym)
            except Exception as e:
                log.warning(f"{sym} fiyat alinamadi: {e}")
                continue
            # ONEMLI: history.add stratejı kontrolunden SONRA cagrilir.
            # Boylece "son N sn'de fiyat dis bandi gecmemis" kontrolu
            # su anki fiyati haric tutar.

            try:
                klines = self.client.fetch_kline(sym, self.cfg.bybit_interval(), limit=200)
                klines = sort_klines_chronological(klines)
            except Exception as e:
                log.warning(f"{sym} kline alinamadi: {e}")
                self.history.add(sym, price)
                continue

            try:
                bands = compute_bands(
                    klines,
                    ema_period=self.cfg.ema_period,
                    atr_period=self.cfg.atr_period,
                    ic_carpan=self.cfg.ic_carpan,
                    dis_carpan=self.cfg.dis_carpan,
                    asiri_carpan=self.cfg.asiri_carpan,
                )
            except Exception as e:
                log.warning(f"{sym} bant hesaplanamadi: {e}")
                self.history.add(sym, price)
                continue

            signal = detect_entry(sym, price, bands, self.history)

            # Kontrol sonrasi history'ye ekle
            self.history.add(sym, price)

            if signal is None:
                continue

            # Sinyal var - giris isimini baslat
            self._try_enter(sym, signal, bands)

    def _try_enter(self, symbol: str, signal: EntrySignal, bands):
        cfg = self.cfg
        instrument = self._instruments.get(symbol)
        if instrument is None:
            log.warning(f"{symbol} instrument cache'inde yok, atlandi.")
            self.state.incr("skip_instrument")
            return

        if instrument["max_leverage"] < cfg.kaldirac:
            self.state.incr("skip_leverage")
            return

        # Bakiye kontrolu
        try:
            balance = self.client.fetch_balance_usdt()
        except Exception as e:
            log.warning(f"Bakiye okunamadi: {e}")
            return

        needed_margin = self.stake_usdt
        if balance < needed_margin:
            # Yetersiz bakiye - mesaj at, deneme yok
            self.state.incr("skip_balance")
            self.tg.insufficient_balance(symbol, signal.side, needed_margin, balance, 0.0)
            return

        # Notional = stake * kaldirac
        notional = self.stake_usdt * cfg.kaldirac
        qty = calc_qty(
            notional, cfg.kaldirac, signal.price,
            instrument["qty_step"], instrument["min_order_qty"],
        )
        if qty <= 0:
            self.state.incr("skip_min_qty")
            self.tg.trade_skipped(symbol, signal.side, signal.price, "Minimum islem miktari altinda")
            return

        bybit_side = "Buy" if signal.side == "LONG" else "Sell"
        fill: FillResult = submit_with_retries(
            self.client, symbol, bybit_side, qty,
            is_entry=True,
            instrument=instrument,
            max_attempts=cfg.giris_deneme,
            wait_seconds=cfg.deneme_bekleme_sn,
        )

        if not fill.filled:
            self.state.incr("skip_unfilled_entry")
            self.tg.entry_order_failed(symbol, signal.side, fill.last_price, fill.attempts, cfg.deneme_bekleme_sn)
            return

        entry_price = fill.avg_price
        filled_qty = fill.qty

        # BE cizgisi
        dis_band = bands.dis_ust if signal.side == "LONG" else bands.dis_alt
        be_line = compute_be_line(signal.side, dis_band, cfg.maker_oran)

        # Stop loss fiyati
        sl_pct = cfg.stoploss_yuzde
        if signal.side == "LONG":
            sl_price = entry_price * (1 - sl_pct / 100.0)
        else:
            sl_price = entry_price * (1 + sl_pct / 100.0)

        # Borsa SL'i yerlestir
        try:
            sl_str = fmt_price(sl_price, instrument["tick_size"])
            self.client.set_position_stop_loss(symbol, sl_str)
        except Exception as e:
            log.warning(f"{symbol} SL set edilemedi: {e}")
            self.tg.error(f"{symbol} SL set edilemedi: {e}")

        # Pozisyon kaydet
        pos = Position(
            symbol=symbol,
            side=signal.side,
            entry_price=entry_price,
            qty=filled_qty,
            stake=self.stake_usdt,
            notional=notional,
            leverage=cfg.kaldirac,
            atr_at_entry=bands.atr,
            be_line=be_line,
            stop_loss_price=sl_price,
        )
        self.state.add_position(pos)
        self.state.incr("opened")

        # Telegram
        self.tg.trade_opened(
            symbol=symbol, side=signal.side,
            entry=entry_price, stake=self.stake_usdt, notional=notional,
            leverage=cfg.kaldirac, qty=filled_qty,
            sl_price=sl_price, sl_pct=sl_pct,
            be_line=be_line,
            atr=bands.atr, ema=bands.ema,
            dis_band=dis_band,
            asiri=(bands.asiri_ust if signal.side == "LONG" else bands.asiri_alt),
        )

    # ---------- Exit loop -------------------------------------------------
    def exit_loop(self):
        log.info("Exit thread basladi.")
        while not SHUTDOWN.is_set():
            t0 = time.time()
            try:
                self._scan_exits()
            except Exception as e:
                log.exception(f"Exit tarama hatasi: {e}")
                try:
                    self.tg.error(f"Exit tarama: {e}")
                except Exception:
                    pass
            elapsed = time.time() - t0
            sleep_for = max(0.5, self.cfg.tarama_saniye - elapsed)
            SHUTDOWN.wait(sleep_for)
        log.info("Exit thread durdu.")

    def _scan_exits(self):
        for pos in self.state.all_open():
            if SHUTDOWN.is_set():
                break
            sym = pos.symbol
            try:
                price = self.client.fetch_last_price(sym)
            except Exception as e:
                log.warning(f"{sym} fiyat alinamadi (exit): {e}")
                continue
            self.history.add(sym, price)

            # Borsa SL tetiklendi mi? -> pozisyon yoksa kapanmis demek
            try:
                positions = self.client.fetch_positions()
                still_open = any(p.get("symbol") == sym and float(p.get("size") or 0) > 0
                                 for p in positions)
            except Exception:
                still_open = True

            if not still_open:
                # Borsa SL tetiklendi (veya manuel kapanma)
                self._finalize_close(pos, price, "Stoploss Exit")
                continue

            # Guncel bantlari hesapla (cikis kontrolu icin dis bant gerekli)
            try:
                klines = self.client.fetch_kline(sym, self.cfg.bybit_interval(), limit=200)
                klines = sort_klines_chronological(klines)
                bands = compute_bands(
                    klines,
                    ema_period=self.cfg.ema_period,
                    atr_period=self.cfg.atr_period,
                    ic_carpan=self.cfg.ic_carpan,
                    dis_carpan=self.cfg.dis_carpan,
                    asiri_carpan=self.cfg.asiri_carpan,
                )
                dis_band_now = bands.dis_ust if pos.side == "LONG" else bands.dis_alt
            except Exception as e:
                log.warning(f"{sym} cikis icin bant hesaplanamadi: {e}")
                continue

            # Seviye guncellemesi
            new_level = update_level_and_ce(
                pos, price,
                self.cfg.stage1_tetik, self.cfg.stage1_takip,
                self.cfg.stage2_tetik, self.cfg.stage2_takip,
                self.cfg.winrate_tetik, self.cfg.winrate_takip,
            )
            if new_level is not None:
                # Seviye Telegram bildirimi
                level_name = LEVEL_LABELS.get(new_level, "?")
                emoji = "📈" if new_level in (LEVEL_CE1, LEVEL_CE2) else ("🚀" if new_level == LEVEL_WINRATE else "🎯")
                trail = self._trail_for_level(new_level)
                next_label, next_price = self._next_target(pos)
                self.tg.level_changed(
                    level_name=level_name, emoji=emoji,
                    symbol=sym, side=pos.side,
                    entry=pos.entry_price, current=price,
                    profit_usdt=pos.profit_usdt(price),
                    profit_pct=pos.profit_pct(price),
                    atr=pos.atr_at_entry,
                    ce_price=pos.ce_price or pos.entry_price,
                    ce_trail_atr=trail,
                    next_target_atr=None,
                    next_target_label=next_label,
                    next_target_price=next_price,
                )

            # Cikis kontrol
            exit_type = check_exit(pos, price, dis_band_now)
            if exit_type:
                self._execute_exit(pos, exit_type)

    def _trail_for_level(self, level: int) -> float:
        if level >= LEVEL_WINRATE:
            return self.cfg.winrate_takip
        if level >= LEVEL_CE2:
            return self.cfg.stage2_takip
        if level >= LEVEL_CE1:
            return self.cfg.stage1_takip
        return 0.0

    def _next_target(self, pos: Position):
        if pos.level >= LEVEL_WINRATE:
            return None, None
        if pos.level == LEVEL_ENTRY:
            label = "CE Stage 1"
        elif pos.level == LEVEL_CE1:
            label = "CE Stage 2"
        else:
            label = "Winrate"
        tgt = next_level_target_price(
            pos,
            self.cfg.stage1_tetik, self.cfg.stage2_tetik, self.cfg.winrate_tetik,
        )
        return label, tgt

    def _execute_exit(self, pos: Position, exit_type: str):
        cfg = self.cfg
        instrument = self._instruments.get(pos.symbol)
        if instrument is None:
            log.warning(f"{pos.symbol} instrument yok, cikis denemiyor.")
            return

        bybit_side = "Sell" if pos.side == "LONG" else "Buy"
        fill = submit_with_retries(
            self.client, pos.symbol, bybit_side, pos.qty,
            is_entry=False,
            instrument=instrument,
            max_attempts=cfg.cikis_deneme,
            wait_seconds=cfg.deneme_bekleme_sn,
            reduce_only=True,
        )
        if not fill.filled:
            # 50 denemede dolmadi - sonraki tarama tekrar dener
            self.tg.exit_order_failed(
                pos.symbol, pos.side, fill.last_price,
                pos.profit_usdt(fill.last_price), fill.attempts,
            )
            return

        self._finalize_close(pos, fill.avg_price, exit_type)

    def _finalize_close(self, pos: Position, exit_price: float, exit_type: str):
        # Pozisyonu state'ten cikar
        removed = self.state.remove_position(pos.symbol)
        if removed is None:
            return

        pnl = pos.profit_usdt(exit_price)
        pnl_pct = pos.profit_pct(exit_price)
        duration_min = int((time.time() - pos.open_time) / 60)

        # Bakiye guncel
        try:
            balance = self.client.fetch_balance_usdt()
        except Exception:
            balance = self.start_balance + pnl

        # Log kayit
        self.state.log_closed({
            "symbol": pos.symbol,
            "side": pos.side,
            "entry": pos.entry_price,
            "exit": exit_price,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "exit_type": exit_type,
            "duration_min": duration_min,
            "close_time": time.time(),
        })
        self.state.incr("closed")

        self.tg.trade_closed(
            symbol=pos.symbol, side=pos.side,
            entry=pos.entry_price, exit_price=exit_price,
            duration_minutes=duration_min,
            pnl_usdt=pnl, pnl_pct=pnl_pct,
            exit_type=exit_type, balance_after=balance,
        )

    # ---------- Report loop -----------------------------------------------
    def report_loop(self):
        log.info("Report thread basladi.")
        # Ilk turde son zamanlari simdiye ayarla, aniden patlamayalim
        self._last_report_10m = time.time()
        self._last_report_hour = time.time()
        self._last_report_12h_date = datetime.now()
        self._last_report_24h_date = datetime.now()

        while not SHUTDOWN.is_set():
            try:
                self._maybe_send_reports()
            except Exception as e:
                log.exception(f"Rapor hatasi: {e}")
            SHUTDOWN.wait(30)  # her 30 saniyede zamani kontrol et
        log.info("Report thread durdu.")

    def _maybe_send_reports(self):
        now = time.time()
        now_dt = datetime.now()

        # 10 dakikalik
        if self.cfg.rapor_10dk and (now - self._last_report_10m >= 600):
            self._send_10min_report()
            self._last_report_10m = now

        # Saatlik (her saatin :00'inde, en az 55 dk sonra)
        if self.cfg.rapor_saatlik and now_dt.minute < 1 and (now - self._last_report_hour >= 55 * 60):
            self._send_hourly_report()
            self._last_report_hour = now

        # 12 saatlik
        if (now_dt.hour == self.cfg.rapor_12saat_saat and now_dt.minute < 1
                and (self._last_report_12h_date is None
                     or self._last_report_12h_date.date() < now_dt.date()
                     or (self._last_report_12h_date.date() == now_dt.date()
                         and self._last_report_12h_date.hour != now_dt.hour))):
            self._send_12h_report()
            self._last_report_12h_date = now_dt

        # 24 saatlik
        if (now_dt.hour == self.cfg.rapor_24saat_saat and now_dt.minute < 1
                and (self._last_report_24h_date is None
                     or self._last_report_24h_date.date() < now_dt.date())):
            self._send_24h_report()
            self._last_report_24h_date = now_dt

    def _send_10min_report(self):
        try:
            balance = self.client.fetch_balance_usdt()
        except Exception:
            balance = 0.0
        # Acik pozisyonlar
        prices = {}
        for pos in self.state.all_open():
            try:
                prices[pos.symbol] = self.client.fetch_last_price(pos.symbol)
            except Exception:
                prices[pos.symbol] = pos.entry_price
        open_text = format_open_positions(
            self.state, prices, {}, self.cfg.stage1_tetik, self.cfg.stage2_tetik, self.cfg.winrate_tetik,
        )
        # Kullanilan marjin tahmini
        used_margin = sum(p.stake for p in self.state.all_open())
        # Son 10dk sayaclari
        snap = self.state.snapshot_counters()
        opened = snap.get("opened", 0)
        closed = snap.get("closed", 0)
        skip_text = format_skipped(self.state)

        self.tg.report_10min(
            open_positions_text=open_text,
            balance=balance,
            used_margin=used_margin,
            opened=opened,
            closed=closed,
            skipped_text=skip_text,
        )
        # 10dk sayaclarini sifirla
        self.state.reset_counter("opened")
        self.state.reset_counter("closed")
        for k in list(snap.keys()):
            if k.startswith("skip_"):
                self.state.reset_counter(k)

    def _send_period_report(self, title_emoji: str, title_text: str, since_ts: float):
        try:
            balance = self.client.fetch_balance_usdt()
        except Exception:
            balance = self.start_balance
        # Kapanan kayitlar
        closed = self.state.closed_since(since_ts)
        period_pnl = sum(r["pnl"] for r in closed)
        # Acik PNL
        open_pnl = 0.0
        for pos in self.state.all_open():
            try:
                p = self.client.fetch_last_price(pos.symbol)
                open_pnl += pos.profit_usdt(p)
            except Exception:
                pass
        skip_text = format_skipped(self.state)
        body = build_period_report(
            title_emoji=title_emoji,
            title_text=title_text,
            period_pnl=period_pnl,
            open_pnl=open_pnl,
            start_balance=balance - period_pnl,
            current_balance=balance,
            closed_records=closed,
            skipped_text=skip_text,
        )
        self.tg.report_period(title_emoji, title_text, body)

    def _send_hourly_report(self):
        since = time.time() - 3600
        self._send_period_report("📊", "1 SAATLIK RAPOR", since)

    def _send_12h_report(self):
        since = time.time() - 12 * 3600
        self._send_period_report("📊", "12 SAATLIK RAPOR", since)

    def _send_24h_report(self):
        since = time.time() - 24 * 3600
        self._send_period_report("📊", "GUNLUK RAPOR (24 SAAT)", since)


# --- Entry point -----------------------------------------------------------
def main():
    sig.signal(sig.SIGINT, _on_signal)
    sig.signal(sig.SIGTERM, _on_signal)

    try:
        cfg = Config(os.environ.get("CONFIG_PATH", "config.json"))
    except Exception as e:
        log.error(f"Config hatasi: {e}")
        sys.exit(1)

    bot = Bot(cfg)
    try:
        bot.setup()
    except Exception as e:
        log.exception(f"Setup hatasi: {e}")
        try:
            TelegramNotifier(cfg.telegram_token, cfg.telegram_chat_id).error(f"Setup hatasi: {e}")
        except Exception:
            pass
        sys.exit(1)

    threads = [
        threading.Thread(target=bot.entry_loop, name="EntryThread", daemon=True),
        threading.Thread(target=bot.exit_loop,  name="ExitThread",  daemon=True),
        threading.Thread(target=bot.report_loop, name="ReportThread", daemon=True),
    ]
    for t in threads:
        t.start()

    log.info("Bot calisiyor. Cikis icin Ctrl+C.")
    # Ana thread: sinyali bekle
    while not SHUTDOWN.is_set():
        time.sleep(1)
    log.info("Kapaniyor...")
    for t in threads:
        t.join(timeout=5)
    log.info("Bot durdu.")


if __name__ == "__main__":
    main()
