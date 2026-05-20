"""
reports.py - Periyodik raporlarin formatlanmasi.
state ve config bilgilerini alir, telegram'a hazir metin uretir.
"""
from collections import defaultdict
from datetime import datetime
from typing import List

from position import LEVEL_LABELS
from state import StateManager


def _coin(symbol: str) -> str:
    return symbol.replace("USDT", "")


def format_open_positions(state: StateManager, current_prices: dict, atr_lookup: dict,
                          stage1_t: float, stage2_t: float, winrate_t: float) -> str:
    lines = []
    for pos in state.all_open():
        cur = current_prices.get(pos.symbol)
        if cur is None:
            cur = pos.entry_price
        pnl = pos.profit_usdt(cur)
        pnl_pct = pos.profit_pct(cur)
        level_label = LEVEL_LABELS.get(pos.level, "?")
        ce_text = f"{pos.ce_price:.6f}" if pos.ce_price is not None else "Pasif"
        lines.append(
            f"┌─ {pos.symbol} {pos.side}\n"
            f"│  Giris: {pos.entry_price:.6f} | Su an: {cur:.6f}\n"
            f"│  Kar: {pnl:+.3f} USDT ({pnl_pct:+.2f}%) | Seviye: {level_label}\n"
            f"│  CE: {ce_text} | BE: {pos.be_line:.6f}\n"
            f"└──"
        )
    return "\n".join(lines)


def format_skipped(state: StateManager, since_key_prefix: str = "skip_") -> str:
    """state counters'tan skip_ ile baslayan butun atlama sebeplerini ozetler."""
    snap = state.snapshot_counters()
    items = [(k, v) for k, v in snap.items() if k.startswith(since_key_prefix) and v > 0]
    if not items:
        return "Atlanan Sinyal: 0\n"
    total = sum(v for _, v in items)
    parts = []
    for k, v in items:
        reason = k.replace(since_key_prefix, "").replace("_", " ")
        parts.append(f"{reason}: {v}")
    return f"Atlanan Sinyal: {total} ({', '.join(parts)})\n"


def build_period_report(
    title_emoji: str,
    title_text: str,
    period_pnl: float,
    open_pnl: float,
    start_balance: float,
    current_balance: float,
    closed_records: list,
    skipped_text: str,
    coin_breakdown_limit: int = 5,
) -> str:
    """Saatlik / 12 saatlik / 24 saatlik rapor govdesi."""
    total = len(closed_records)
    wins = sum(1 for r in closed_records if r["pnl"] > 0)
    losses = sum(1 for r in closed_records if r["pnl"] < 0)
    even = sum(1 for r in closed_records if r["pnl"] == 0)
    winrate = (wins / total * 100.0) if total > 0 else 0.0

    total_win = sum(r["pnl"] for r in closed_records if r["pnl"] > 0)
    total_loss = sum(r["pnl"] for r in closed_records if r["pnl"] < 0)

    # Cikis tipleri
    by_exit = defaultdict(lambda: {"count": 0, "pnl": 0.0})
    for r in closed_records:
        e = r.get("exit_type", "?")
        by_exit[e]["count"] += 1
        by_exit[e]["pnl"] += r["pnl"]

    exit_lines = []
    for name in ["Winrate Exit", "CE2 Exit", "CE1 Exit", "Lose Exit", "Stoploss Exit"]:
        b = by_exit.get(name, {"count": 0, "pnl": 0.0})
        exit_lines.append(f"{name}: {b['count']} | {b['pnl']:+.2f} USDT")

    # Coin bazli
    by_coin = defaultdict(lambda: {"count": 0, "pnl": 0.0, "w": 0, "l": 0})
    for r in closed_records:
        sym = r["symbol"]
        by_coin[sym]["count"] += 1
        by_coin[sym]["pnl"] += r["pnl"]
        if r["pnl"] > 0:
            by_coin[sym]["w"] += 1
        elif r["pnl"] < 0:
            by_coin[sym]["l"] += 1

    sorted_coins = sorted(by_coin.items(), key=lambda x: x[1]["pnl"], reverse=True)
    best = sorted_coins[:coin_breakdown_limit]
    worst = sorted(by_coin.items(), key=lambda x: x[1]["pnl"])[:coin_breakdown_limit]

    def coin_lines(items):
        out = []
        for sym, b in items:
            out.append(
                f"{sym:10s} → {b['count']} islem | {b['pnl']:+.2f} USDT | W:{b['w']} K:{b['l']}"
            )
        return "\n".join(out) if out else "Yok"

    body = (
        f"💰 GENEL DURUM\n"
        f"Baslangic: {start_balance:.2f} USDT → Su an: {current_balance:.2f} USDT\n"
        f"Donem PNL: {period_pnl:+.2f} USDT\n"
        f"Acik PNL: {open_pnl:+.2f} USDT\n\n"
        f"⚡ ISLEM OZETI\n"
        f"Toplam: {total} | Kazanan: {wins} | Kaybeden: {losses} | Basabas: {even}\n"
        f"Winrate: %{winrate:.1f}\n"
        f"Toplam Kazanc: {total_win:+.2f} USDT | Toplam Kayip: {total_loss:+.2f} USDT\n\n"
        f"🏆 CIKIS TIPLERI\n" + "\n".join(exit_lines) + "\n\n"
        f"🪙 EN IYI {coin_breakdown_limit} COIN\n" + coin_lines(best) + "\n\n"
        f"🪙 EN KOTU {coin_breakdown_limit} COIN\n" + coin_lines(worst) + "\n\n"
        f"⚠️ HATALAR\n" + skipped_text
    )

    return f"{title_emoji} {title_text}\n\n" + body
