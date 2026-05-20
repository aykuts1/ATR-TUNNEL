"""
telegram.py - Tum Telegram bildirimleri ve Z raporlari.
Senkron http istegi, basit ve dayanikli.
"""
from datetime import datetime
from typing import Iterable, List, Optional

import requests


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self.api_base = f"https://api.telegram.org/bot{token}"

    # --- Temel gonderim ----------------------------------------------------
    def send(self, text: str) -> None:
        if not self.token or not self.chat_id:
            return
        try:
            requests.post(
                f"{self.api_base}/sendMessage",
                data={"chat_id": self.chat_id, "text": text},
                timeout=10,
            )
        except Exception:
            # Telegram hatasi botun calismasini engellemez
            pass

    @staticmethod
    def _now() -> str:
        return datetime.now().strftime("%H:%M | %d.%m.%Y")

    @staticmethod
    def _coin(symbol: str) -> str:
        return symbol.replace("USDT", "")

    # --- Bot baslatildi ----------------------------------------------------
    def bot_started(
        self,
        balance: float,
        stake: float,
        leverage: int,
        max_positions: int,
        coin_count: int,
        timeframe_min: int,
        stoploss_pct: float,
        existing_positions: List[str],
        unsupported_leverage: List[dict],  # [{"symbol":..., "max":...}]
        ema_period: int,
        atr_period: int,
        ic_carpan: float,
        dis_carpan: float,
        asiri_carpan: float,
        lookback_sec: int,
        scan_sec: int,
        stage1_t: float, stage1_k: float,
        stage2_t: float, stage2_k: float,
        winrate_t: float, winrate_k: float,
        entry_attempts: int,
        wait_sec: int,
    ) -> None:
        text = (
            f"🟢 BOT BASLATILDI — {self._now()}\n\n"
            f"💰 Bakiye: {balance:.2f} USDT\n"
            f"📊 Stake: {stake:.2f} USDT\n"
            f"⚡ Kaldirac: {leverage}x | Max {max_positions} Islem\n"
            f"📈 Izlenen Coin: {coin_count} | Timeframe: {timeframe_min}dk\n"
            f"🔴 Stop Loss: %{stoploss_pct}\n"
            f"📌 Mevcut Pozisyon: {len(existing_positions)} (bot kontrolunde degil)\n"
        )
        if existing_positions:
            text += "   " + ", ".join(self._coin(s) for s in existing_positions) + "\n"

        text += (
            f"\n📊 BANT AYARLARI\n"
            f"EMA: {ema_period} | ATR: {atr_period}\n"
            f"Ic Carpan: {ic_carpan} | Dis Carpan: {dis_carpan} | Asiri: {asiri_carpan}\n"
            f"Lookback: {lookback_sec}sn | Tarama: {scan_sec}sn\n"
            f"\n📈 GIRIS\n"
            f"Max Deneme: {entry_attempts} | Bekleme: {wait_sec}sn\n"
            f"\n📉 CIKIS SISTEMI\n"
            f"CE Stage 1: {stage1_t} ATR karda → {stage1_k} ATR takip\n"
            f"CE Stage 2: {stage2_t} ATR karda → {stage2_k} ATR takip\n"
            f"Winrate:    {winrate_t} ATR karda → {winrate_k} ATR takip\n"
        )
        if unsupported_leverage:
            text += "\n⚠️ 50X DESTEKLEMEYENLER:\n"
            for u in unsupported_leverage:
                text += f"• {u['symbol']} — Maks {int(u['max'])}x\n"

        self.send(text)

    # --- Islem acildi ------------------------------------------------------
    def trade_opened(
        self,
        symbol: str, side: str,
        entry: float, stake: float, notional: float,
        leverage: int, qty: float,
        sl_price: float, sl_pct: float,
        be_line: float,
        atr: float, ema: float,
        dis_band: float, asiri: float,
    ) -> None:
        text = (
            f"✅ ISLEM ACILDI — {self._now()}\n\n"
            f"📌 {symbol} — {side}\n"
            f"💵 Giris Fiyati: {entry:.6f}\n"
            f"📊 Stake: {stake:.2f} USDT | Hacim: {notional:.2f} USDT\n"
            f"⚡ Kaldirac: {leverage}x | Miktar: {qty}\n"
            f"🛑 Stop Loss: {sl_price:.6f} (%{sl_pct})\n"
            f"📉 BE Cizgisi: {be_line:.6f}\n"
            f"📊 ATR: {atr:.6f} | EMA: {ema:.6f}\n"
            f"🔴 Dis Bant: {dis_band:.6f} | Asiri Cizgi: {asiri:.6f}\n"
        )
        self.send(text)

    # --- Seviye gecisleri --------------------------------------------------
    def level_changed(
        self,
        level_name: str, emoji: str,
        symbol: str, side: str,
        entry: float, current: float,
        profit_usdt: float, profit_pct: float,
        atr: float, ce_price: float,
        ce_trail_atr: float,
        next_target_atr: Optional[float],
        next_target_label: Optional[str],
        next_target_price: Optional[float],
    ) -> None:
        text = (
            f"{emoji} {level_name} AKTIF — {self._now()}\n\n"
            f"📌 {symbol} — {side}\n"
            f"💵 Giris: {entry:.6f} → Su an: {current:.6f}\n"
            f"💰 Kar: {profit_usdt:+.3f} USDT ({profit_pct:+.2f}%)\n"
            f"📊 ATR: {atr:.6f}\n"
            f"📉 CE Seviyesi: {ce_price:.6f} ({ce_trail_atr} ATR takip)\n"
        )
        if next_target_label and next_target_price is not None:
            text += f"🎯 {next_target_label} icin: {next_target_price:.6f} gerekli\n"
        self.send(text)

    # --- Islem kapandi -----------------------------------------------------
    def trade_closed(
        self,
        symbol: str, side: str,
        entry: float, exit_price: float,
        duration_minutes: int,
        pnl_usdt: float, pnl_pct: float,
        exit_type: str, balance_after: float,
    ) -> None:
        emoji = "🟢" if pnl_usdt >= 0 else "🔴"
        text = (
            f"{emoji} ISLEM KAPANDI — {self._now()}\n\n"
            f"📌 {symbol} — {side}\n"
            f"💵 Giris: {entry:.6f} | Cikis: {exit_price:.6f}\n"
            f"⏱ Sure: {duration_minutes} dakika\n"
            f"💰 Kar/Zarar: {pnl_usdt:+.3f} USDT ({pnl_pct:+.2f}%)\n"
            f"📝 Cikis Tipi: {exit_type}\n"
            f"🏦 Guncel Bakiye: {balance_after:.2f} USDT\n"
        )
        self.send(text)

    # --- Uyarilar ----------------------------------------------------------
    def trade_skipped(self, symbol: str, side: str, price: float, reason: str, extra: str = "") -> None:
        text = (
            f"⚠️ ISLEM ACILAMADI — {self._now()}\n\n"
            f"📌 {symbol} — {side}\n"
            f"💵 Sinyal Fiyati: {price:.6f}\n"
            f"❌ Sebep: {reason}\n"
        )
        if extra:
            text += extra + "\n"
        self.send(text)

    def insufficient_balance(self, symbol: str, side: str, needed: float, available: float, locked: float) -> None:
        text = (
            f"⚠️ YETERSIZ BAKIYE — {self._now()}\n\n"
            f"📌 {symbol} — {side}\n"
            f"❌ Gerekli Marjin: {needed:.2f} USDT\n"
            f"💰 Mevcut Bakiye: {available:.2f} USDT\n"
            f"📉 Acik Pozisyon Marjini: {locked:.2f} USDT\n"
        )
        self.send(text)

    def entry_order_failed(self, symbol: str, side: str, last_price: float, attempts: int, wait_sec: int) -> None:
        text = (
            f"⚠️ GIRIS EMRI DOLMADI — {self._now()}\n\n"
            f"📌 {symbol} — {side}\n"
            f"❌ {attempts} denemede emir dolmadi\n"
            f"💵 Son Emir Fiyati: {last_price:.6f}\n"
            f"⏱ Deneme Suresi: ~{attempts * wait_sec / 60:.1f} dakika\n"
            f"📝 Sinyal atlandi\n"
        )
        self.send(text)

    def exit_order_failed(self, symbol: str, side: str, last_price: float, profit_usdt: float, attempts: int) -> None:
        text = (
            f"⚠️ CIKIS EMRI DOLMADI — {self._now()}\n\n"
            f"📌 {symbol} — {side}\n"
            f"❌ {attempts} denemede kapanmadi\n"
            f"💵 Son Emir Fiyati: {last_price:.6f}\n"
            f"💰 Anlik Kar: {profit_usdt:+.3f} USDT\n"
            f"📝 5 saniye sonra tekrar denenecek\n"
        )
        self.send(text)

    def error(self, message: str) -> None:
        self.send(f"🚨 HATA — {self._now()}\n\n{message}")

    # --- Z raporlari -------------------------------------------------------
    def report_10min(self, open_positions_text: str, balance: float, used_margin: float,
                     opened: int, closed: int, skipped_text: str) -> None:
        text = (
            f"📊 10 DAKIKA RAPORU — {self._now()}\n\n"
            f"⚡ ACIK POZISYONLAR\n"
            f"{open_positions_text if open_positions_text else 'Yok'}\n\n"
            f"💰 ANLIK DURUM\n"
            f"Bakiye: {balance:.2f} USDT | Kullanilan Marjin: {used_margin:.2f} USDT\n\n"
            f"📝 SON 10 DAKIKA\n"
            f"Acilan: {opened} | Kapanan: {closed}\n"
            f"{skipped_text}"
        )
        self.send(text)

    def report_period(self, title: str, period_label: str, body: str) -> None:
        text = f"{title} — {self._now()}\n\n{body}"
        self.send(text)
