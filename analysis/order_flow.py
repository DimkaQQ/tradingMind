"""
analysis/order_flow.py — Order Flow Analysis
──────────────────────────────────────────────
Концепция: Анализ потока ордеров показывает реальный дисбаланс между
покупателями и продавцами, который свечи скрывают.

Что считаем (без тикового потока, только OHLCV):
  1. Delta (Buy Volume - Sell Volume) через OHLCV proxy
  2. Cumulative Delta — накопленный дисбаланс
  3. Volume Imbalance — аномальные объёмы
  4. Absorption — поглощение (объём растёт, но цена не двигается)
  5. Effort vs Result — соответствие объёма и движения цены
  6. POC смещение — куда идёт Point of Control
"""

import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)


class OrderFlowAnalyzer:

    def analyze(self, df: pd.DataFrame) -> dict:
        if len(df) < 20:
            return self._empty()

        close  = df["Close"]
        open_  = df["Open"]
        high   = df["High"]
        low    = df["Low"]
        volume = df["Volume"]

        # ── 1. Delta proxy ───────────────────────────────────────────────────
        # Метод: если закрытие > открытия → buy volume ≈ пропорционально
        # позиции закрытия в диапазоне свечи
        buy_ratio  = (close - low) / (high - low + 1e-10)
        sell_ratio = 1 - buy_ratio
        buy_vol  = volume * buy_ratio
        sell_vol = volume * sell_ratio
        delta    = buy_vol - sell_vol

        # ── 2. Cumulative Delta ───────────────────────────────────────────────
        cum_delta = delta.cumsum()
        # Тренд кумулятивной дельты за последние 20 свечей
        cd_recent = cum_delta.tail(20)
        cd_slope = float(cd_recent.iloc[-1] - cd_recent.iloc[0])

        # Расхождение: цена падает, дельта растёт → скрытое накопление
        price_slope  = float(close.tail(10).iloc[-1] - close.tail(10).iloc[0])
        delta_slope  = float(delta.tail(10).sum())
        divergence   = self._detect_divergence(price_slope, delta_slope, close, cum_delta)

        # ── 3. Volume Imbalance ───────────────────────────────────────────────
        vol_mean = volume.rolling(20).mean()
        vol_std  = volume.rolling(20).std()
        imbalances = []
        for i in range(len(df) - 5, len(df)):
            if i < 0:
                continue
            z_score = (float(volume.iloc[i]) - float(vol_mean.iloc[i])) / (float(vol_std.iloc[i]) + 1e-10)
            if z_score > 2.0:
                candle_dir = "bullish" if float(close.iloc[i]) > float(open_.iloc[i]) else "bearish"
                imbalances.append({
                    "z_score": round(z_score, 1),
                    "direction": candle_dir,
                    "price": round(float(close.iloc[i]), 4),
                })

        # ── 4. Absorption (поглощение) ────────────────────────────────────────
        # Высокий объём + маленькое движение = поглощение
        absorption = self._detect_absorption(close, volume, high, low)

        # ── 5. Effort vs Result ───────────────────────────────────────────────
        effort_result = self._effort_vs_result(close, volume, high, low)

        # ── 6. Delta Exhaustion ───────────────────────────────────────────────
        # Дельта резко меняет направление → истощение тренда
        recent_delta = delta.tail(5)
        delta_exhaustion = ""
        if len(recent_delta) >= 3:
            last_3_signs = np.sign(recent_delta.values[-3:])
            if last_3_signs[-1] != last_3_signs[-2] and abs(float(delta.iloc[-1])) > float(vol_mean.iloc[-1]) * 0.3:
                delta_exhaustion = "🔄 Разворот дельты — возможное истощение тренда"

        # ── 7. Итоговый скор и сигнал ─────────────────────────────────────────
        score, signal, summary = self._score(
            cd_slope, divergence, imbalances, absorption,
            effort_result, delta_exhaustion, delta
        )

        return {
            "signal":             signal,
            "score":              score,
            "summary":            summary,
            "cum_delta_slope":    round(cd_slope, 0),
            "divergence":         divergence,
            "volume_imbalances":  imbalances[-3:],
            "absorption":         absorption,
            "effort_result":      effort_result,
            "delta_exhaustion":   delta_exhaustion,
            "last_delta":         round(float(delta.iloc[-1]), 0),
            "last_buy_vol":       round(float(buy_vol.iloc[-1]), 0),
            "last_sell_vol":      round(float(sell_vol.iloc[-1]), 0),
        }

    # ── Расхождение дельты и цены ─────────────────────────────────────────────
    def _detect_divergence(self, price_slope, delta_slope, close, cum_delta) -> str:
        if abs(price_slope) < float(close.std()) * 0.1:
            return ""  # нет значимого движения

        # Бычье расхождение: цена вниз, дельта вверх
        if price_slope < 0 and delta_slope > 0:
            return "🟢 Бычье расхождение: цена падает, но покупатели накапливают"

        # Медвежье расхождение: цена вверх, дельта вниз
        if price_slope > 0 and delta_slope < 0:
            return "🔴 Медвежье расхождение: цена растёт, но продавцы давят"

        return ""

    # ── Поглощение ────────────────────────────────────────────────────────────
    def _detect_absorption(self, close, volume, high, low) -> str:
        """Большой объём + маленькое тело свечи = поглощение давления"""
        n = len(close)
        if n < 5:
            return ""

        vol_mean = float(volume.tail(20).mean())
        for i in range(n-3, n):
            body   = abs(float(close.iloc[i]) - float(close.iloc[i-1]))
            range_ = float(high.iloc[i]) - float(low.iloc[i])
            vol    = float(volume.iloc[i])

            if vol > vol_mean * 1.8 and body < range_ * 0.25:
                direction = "бычье" if float(close.iloc[i]) > float(close.iloc[i-1]) else "медвежье"
                return f"🧽 Поглощение ({direction}): высокий объём, маленькое тело → крупный игрок поглощает"

        return ""

    # ── Effort vs Result ──────────────────────────────────────────────────────
    def _effort_vs_result(self, close, volume, high, low) -> str:
        """
        Большой объём + маленькое движение = слабость
        Маленький объём + большое движение = сила
        """
        n = len(close)
        if n < 5:
            return ""

        vol_mean   = float(volume.tail(20).mean())
        range_mean = float((high - low).tail(20).mean())

        last_vol   = float(volume.iloc[-1])
        last_range = float(high.iloc[-1]) - float(low.iloc[-1])
        last_dir   = "вверх" if float(close.iloc[-1]) > float(close.iloc[-2]) else "вниз"

        if last_vol > vol_mean * 1.5 and last_range < range_mean * 0.5:
            return f"⚡ Слабый результат на большом объёме ({last_dir}) — возможное поглощение или разворот"

        if last_vol < vol_mean * 0.6 and last_range > range_mean * 1.5:
            return f"✅ Сильное движение {last_dir} на низком объёме — нет сопротивления"

        return ""

    # ── Скоринг ───────────────────────────────────────────────────────────────
    def _score(self, cd_slope, divergence, imbalances, absorption,
               effort_result, delta_exhaustion, delta) -> tuple:
        score = 0
        signals = []

        # Cumulative delta slope
        norm = abs(float(delta.std())) * 20 + 1e-10
        cd_normalized = max(-40, min(40, int(cd_slope / norm * 40)))
        score += cd_normalized
        if abs(cd_normalized) > 10:
            signals.append(f"{'📈' if cd_normalized > 0 else '📉'} Cum.Delta {'бычья' if cd_normalized > 0 else 'медвежья'}")

        # Расхождение
        if "Бычье" in divergence:  score += 25; signals.append(divergence)
        if "Медвежье" in divergence: score -= 25; signals.append(divergence)

        # Дисбалансы объёма
        for imb in imbalances:
            if imb["direction"] == "bullish": score += 8
            else: score -= 8

        # Поглощение
        if "бычье" in absorption: score += 15; signals.append(absorption)
        elif "медвежье" in absorption: score -= 15; signals.append(absorption)

        # Effort vs result
        if effort_result: signals.append(effort_result)

        # Delta exhaustion
        if delta_exhaustion: signals.append(delta_exhaustion)

        score = max(-100, min(100, score))
        signal = "bullish" if score > 20 else ("bearish" if score < -20 else "neutral")
        summary = " | ".join(signals) if signals else "Дисбалансов не обнаружено"

        return score, signal, summary

    def _empty(self):
        return {
            "signal": "neutral", "score": 0,
            "summary": "Недостаточно данных",
            "cum_delta_slope": 0, "divergence": "",
            "volume_imbalances": [], "absorption": "",
            "effort_result": "", "delta_exhaustion": "",
            "last_delta": 0, "last_buy_vol": 0, "last_sell_vol": 0,
        }
