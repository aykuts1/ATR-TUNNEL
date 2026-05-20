"""
config.py - config.json dosyasini okur ve dogrular.
Hatali ayar varsa bot baslatmaz, anlasilir hata mesaji verir.
"""
import json
import os
from pathlib import Path


class Config:
    def __init__(self, config_path: str = "config.json"):
        self.path = Path(config_path)
        self._data = self._load()

        # Bant ayarlari
        bant = self._data["bant_ayarlari"]
        self.ema_period = int(bant["ema_period"])
        self.atr_period = int(bant["atr_period"])
        self.ic_carpan = float(bant["ic_carpan"])
        self.dis_carpan = float(bant["dis_carpan"])
        self.asiri_carpan = float(bant["asiri_carpan"])

        # Giris ayarlari
        giris = self._data["giris_ayarlari"]
        self.lookback_saniye = int(giris["lookback_saniye"])
        self.tarama_saniye = int(giris["tarama_saniye"])
        self.timeframe_dakika = int(giris["timeframe_dakika"])

        # Komisyon
        self.maker_oran = float(self._data["komisyon"]["maker_oran"])

        # Cikis ayarlari
        cikis = self._data["cikis_ayarlari"]
        self.stage1_tetik = float(cikis["stage1_tetik"])
        self.stage1_takip = float(cikis["stage1_takip"])
        self.stage2_tetik = float(cikis["stage2_tetik"])
        self.stage2_takip = float(cikis["stage2_takip"])
        self.winrate_tetik = float(cikis["winrate_tetik"])
        self.winrate_takip = float(cikis["winrate_takip"])

        # Pozisyon ayarlari
        poz = self._data["pozisyon_ayarlari"]
        self.stake_yuzde = float(poz["stake_yuzde"])
        self.kaldirac = int(poz["kaldirac"])
        self.max_pozisyon = int(poz["max_pozisyon"])
        self.stoploss_yuzde = float(poz["stoploss_yuzde"])
        self.giris_deneme = int(poz["giris_deneme"])
        self.cikis_deneme = int(poz["cikis_deneme"])
        self.deneme_bekleme_sn = int(poz["deneme_bekleme_sn"])

        # Rapor ayarlari
        rapor = self._data["rapor_ayarlari"]
        self.rapor_10dk = bool(rapor["rapor_10dk"])
        self.rapor_saatlik = bool(rapor["rapor_saatlik"])
        self.rapor_12saat_saat = int(rapor["rapor_12saat_saat"])
        self.rapor_24saat_saat = int(rapor["rapor_24saat_saat"])

        # Coinler
        self.coinler = [c.upper().strip() for c in self._data["coinler"]]

        # Env'den okunan API anahtarlari
        self.bybit_api_key = os.environ.get("BYBIT_API_KEY", "")
        self.bybit_api_secret = os.environ.get("BYBIT_API_SECRET", "")
        self.telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

        self._validate()

    def _load(self):
        if not self.path.exists():
            raise FileNotFoundError(f"config.json bulunamadi: {self.path}")
        with open(self.path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _validate(self):
        # Bant ayarlari
        assert self.ema_period > 0, "ema_period > 0 olmali"
        assert self.atr_period > 0, "atr_period > 0 olmali"
        assert self.ic_carpan > 0, "ic_carpan > 0 olmali"
        assert self.dis_carpan > self.ic_carpan, "dis_carpan ic_carpan'dan buyuk olmali"
        assert self.asiri_carpan > 0, "asiri_carpan > 0 olmali"

        # Giris
        assert self.lookback_saniye > 0, "lookback_saniye > 0 olmali"
        assert self.tarama_saniye > 0, "tarama_saniye > 0 olmali"
        assert self.timeframe_dakika in (1, 3, 5, 15, 30, 60, 120, 240, 360, 720, 1440), \
            "timeframe_dakika Bybit'in destekledigi degerlerden biri olmali"

        # Cikis
        assert self.stage1_tetik > 0, "stage1_tetik > 0 olmali"
        assert self.stage2_tetik > self.stage1_tetik, "stage2_tetik > stage1_tetik olmali"
        assert self.winrate_tetik > self.stage2_tetik, "winrate_tetik > stage2_tetik olmali"

        # Pozisyon
        assert 0 < self.stake_yuzde <= 100, "stake_yuzde 0-100 araliginda olmali"
        assert self.kaldirac > 0, "kaldirac > 0 olmali"
        assert self.max_pozisyon > 0, "max_pozisyon > 0 olmali"
        assert self.stoploss_yuzde > 0, "stoploss_yuzde > 0 olmali"
        assert self.giris_deneme > 0, "giris_deneme > 0 olmali"
        assert self.cikis_deneme > 0, "cikis_deneme > 0 olmali"
        assert self.deneme_bekleme_sn > 0, "deneme_bekleme_sn > 0 olmali"

        # Rapor
        assert 0 <= self.rapor_12saat_saat <= 23, "rapor_12saat_saat 0-23"
        assert 0 <= self.rapor_24saat_saat <= 23, "rapor_24saat_saat 0-23"

        # Coinler
        assert len(self.coinler) > 0, "Coin listesi bos olamaz"
        for c in self.coinler:
            assert c.endswith("USDT"), f"Coin USDT pariteli olmali: {c}"

        # API anahtarlari
        if not self.bybit_api_key or not self.bybit_api_secret:
            raise ValueError("BYBIT_API_KEY ve BYBIT_API_SECRET environment variable olarak ayarlanmali")
        if not self.telegram_token or not self.telegram_chat_id:
            raise ValueError("TELEGRAM_BOT_TOKEN ve TELEGRAM_CHAT_ID environment variable olarak ayarlanmali")

    def bybit_interval(self) -> str:
        """Bybit API icin string formatinda interval."""
        return str(self.timeframe_dakika)
