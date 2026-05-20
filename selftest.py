"""
selftest.py - Bot canliya cikmadan once tum baglantilari ve ayarlari test eder.

Kullanim:
    python selftest.py

Telegram'a tum test sonuclarini gondererek bilgilendirir.
Hicbir trade ACMAZ. Sadece kontrol amacli.
"""
import os
import sys
import time
import traceback

from config import Config
from bybit import BybitClient
from notifier import TelegramNotifier
from bands import compute_bands, sort_klines_chronological


def main():
    print("=" * 60)
    print("BOT SELF-TEST BASLATILIYOR")
    print("=" * 60)

    results = []  # (name, ok, detail)

    # 1) Config
    try:
        cfg = Config(os.environ.get("CONFIG_PATH", "config.json"))
        results.append(("Config Yukleme", True, f"{len(cfg.coinler)} coin, kaldirac {cfg.kaldirac}x"))
        print(f"[OK] Config yuklendi: {len(cfg.coinler)} coin")
    except Exception as e:
        print(f"[HATA] Config: {e}")
        traceback.print_exc()
        results.append(("Config Yukleme", False, str(e)))
        _summary(None, results)
        sys.exit(1)

    # 2) Telegram baglantisi
    tg = TelegramNotifier(cfg.telegram_token, cfg.telegram_chat_id)
    try:
        tg.send("🔍 SELF-TEST BASLADI — Bot baglantilari kontrol ediliyor...")
        results.append(("Telegram Mesaj", True, "Test mesaji gonderildi"))
        print("[OK] Telegram baglantisi")
    except Exception as e:
        print(f"[HATA] Telegram: {e}")
        results.append(("Telegram Mesaj", False, str(e)))

    # 3) Bybit baglantisi + bakiye
    client = None
    balance = 0.0
    try:
        client = BybitClient(cfg.bybit_api_key, cfg.bybit_api_secret, testnet=False)
        balance = client.fetch_balance_usdt()
        results.append(("Bybit Baglanti", True, f"Bakiye: {balance:.2f} USDT"))
        print(f"[OK] Bybit baglanti, bakiye: {balance:.2f} USDT")
    except Exception as e:
        print(f"[HATA] Bybit: {e}")
        traceback.print_exc()
        results.append(("Bybit Baglanti", False, str(e)))
        _summary(tg, results)
        sys.exit(1)

    # 4) Bakiye yeterli mi?
    needed = cfg.stake_yuzde / 100.0 * balance if balance > 0 else 0
    if balance < 10:
        results.append(("Bakiye Yeterli", False, f"Bakiye cok dusuk: {balance:.2f} USDT"))
        print(f"[UYARI] Bakiye cok dusuk: {balance:.2f}")
    else:
        results.append(("Bakiye Yeterli", True, f"Stake: {needed:.2f} USDT"))
        print(f"[OK] Stake olacak: {needed:.2f} USDT")

    # 5) Coin instrument bilgileri + kaldirac desteği
    unsupported = []
    not_found = []
    ok_count = 0
    for sym in cfg.coinler:
        try:
            info = client.get_instrument_info(sym)
            if info["max_leverage"] < cfg.kaldirac:
                unsupported.append((sym, info["max_leverage"]))
            else:
                ok_count += 1
        except Exception as e:
            not_found.append((sym, str(e)[:80]))

    if not_found:
        results.append(("Coin Instrument", False, f"{len(not_found)} coin bulunamadi"))
        for sym, e in not_found:
            print(f"[HATA] {sym}: {e}")
    else:
        results.append(("Coin Instrument", True, f"{ok_count}/{len(cfg.coinler)} coin {cfg.kaldirac}x destekliyor"))
        print(f"[OK] Coin kontrolu: {ok_count} destekli, {len(unsupported)} desteksiz")

    # 6) Kline + bant hesaplama testi (ilk coinde)
    test_sym = cfg.coinler[0]
    try:
        kl = client.fetch_kline(test_sym, cfg.bybit_interval(), limit=200)
        kl = sort_klines_chronological(kl)
        b = compute_bands(
            kl, cfg.ema_period, cfg.atr_period,
            cfg.ic_carpan, cfg.dis_carpan, cfg.asiri_carpan,
        )
        last = client.fetch_last_price(test_sym)
        results.append(("Bant Hesaplama", True,
                        f"{test_sym}: fiyat={last:.4f}, EMA={b.ema:.4f}, ATR={b.atr:.4f}"))
        print(f"[OK] Bant hesaplama: {test_sym} EMA={b.ema:.4f} ATR={b.atr:.4f}")
    except Exception as e:
        results.append(("Bant Hesaplama", False, str(e)))
        print(f"[HATA] Bant: {e}")

    # 7) Acik pozisyon kontrolu (varsa say)
    try:
        positions = client.fetch_positions()
        results.append(("Mevcut Pozisyon", True, f"{len(positions)} acik pozisyon"))
        print(f"[OK] Mevcut pozisyon sayisi: {len(positions)}")
    except Exception as e:
        results.append(("Mevcut Pozisyon", False, str(e)))
        print(f"[HATA] Pozisyon listesi: {e}")

    # 8) Set leverage testi (sadece ilk coinde, gercekten ayarlar!)
    try:
        info = client.get_instrument_info(test_sym)
        if info["max_leverage"] >= cfg.kaldirac:
            client.switch_isolated(test_sym, cfg.kaldirac)
            client.set_leverage(test_sym, cfg.kaldirac)
            results.append(("Kaldirac Ayari", True, f"{test_sym} {cfg.kaldirac}x isolated"))
            print(f"[OK] Kaldirac ayarlandi: {test_sym} {cfg.kaldirac}x isolated")
    except Exception as e:
        results.append(("Kaldirac Ayari", False, str(e)))
        print(f"[HATA] Kaldirac: {e}")

    _summary(tg, results, unsupported=unsupported, balance=balance)


def _summary(tg, results, unsupported=None, balance=None):
    """Telegram'a ozet gonder + console'a yaz."""
    ok_count = sum(1 for _, ok, _ in results if ok)
    fail_count = len(results) - ok_count

    text = f"🔍 SELF-TEST SONUC\n\n"
    text += f"✅ Basarili: {ok_count}\n❌ Hatali: {fail_count}\n\n"
    for name, ok, detail in results:
        icon = "✅" if ok else "❌"
        text += f"{icon} {name}\n   {detail}\n"

    if unsupported:
        text += f"\n⚠️ 50X DESTEKLEMEYENLER ({len(unsupported)}):\n"
        for sym, mx in unsupported:
            text += f"• {sym} → max {int(mx)}x\n"

    if balance is not None and balance > 0:
        text += f"\n💰 Mevcut Bakiye: {balance:.2f} USDT\n"

    if fail_count == 0:
        text += "\n🟢 BOT CALISTIRMAYA HAZIR"
    else:
        text += "\n🔴 BOT CALISMAYA HAZIR DEGIL — once hatalari duzelt"

    print()
    print("=" * 60)
    print(f"OZET: {ok_count} OK, {fail_count} HATA")
    print("=" * 60)

    if tg:
        try:
            tg.send(text)
        except Exception as e:
            print(f"Telegram ozet gonderilemedi: {e}")


if __name__ == "__main__":
    main()
