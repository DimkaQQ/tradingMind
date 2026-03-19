"""
analysis/anchored_vwap.py — Anchored VWAP
────────────────────────────────────────────
Концепция: Обычный VWAP сбрасывается каждый день. Anchored VWAP
считается от ЗНАЧИМОГО события (крупный лоу, крупный хай, начало тренда)
и показывает среднюю цену входа институционалов с этой точки.

Якорные точки:
  1. Yearly High / Low (начало года)
  2. Swing High / Swing Low (последние значимые экстремумы)
  3. High Volume Node (свеча с аномальным объёмом)
  4. Last ATH / ATL в выборке
"""

import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)


class AnchoredVWAPAnalyzer:

    def analyze(self, df: pd.DataFrame) -> dict:
        if len(df) < 20:
            return self._empty()

        close  = df["Close"]
        high   = df["High"]
        low    = df["Low"]
        volume = df["Volume"]
        last_price = float(close.iloc[-1])

        typical = (high + low + close) / 3

        # ── Якорные точки ────────────────────────────────────────────────────
        anchors = {}

        # 1. От начала данных (максимально длинный AVWAP)
        anchors["session"] = self._compute_vwap(typical, volume, 0)

        # 2. Swing High (последний значимый хай)
        sh_idx = self._find_swing_high(high, lookback=50)
        if sh_idx is not None:
            anchors["swing_high"] = self._compute_vwap(typical, volume, sh_idx)

        # 3. Swing Low (последний значимый лоу)
        sl_idx = self._find_swing_low(low, lookback=50)
        if sl_idx is not None:
            anchors["swing_low"] = self._compute_vwap(typical, volume, sl_idx)

        # 4. High Volume Node (свеча с максимальным объёмом за последние 50 свечей)
        hvn_idx = self._find_hvn(volume, lookback=50)
        if hvn_idx is not None:
            anchors["hvn"] = self._compute_vwap(typical, volume, hvn_idx)

        # 5. Последний ATH / ATL в данных
        ath_idx = int(high.tail(60).idxmax()) if len(high) >= 60 else int(high.idxmax())
        atl_idx = int(low.tail(60).idxmin())  if len(low)  >= 60 else int(low.idxmin())
        ath_idx_pos = high.index.get_loc(high.index[ath_idx]) if hasattr(high.index, 'get_loc') else ath_idx
        atl_idx_pos = low.index.get_loc(low.index[atl_idx])   if hasattr(low.index, 'get_loc')  else atl_idx
        # Используем относительную позицию
        ath_pos = len(df) - len(df.tail(60)) + int(high.tail(60).values.argmax())
        atl_pos = len(df) - len(df.tail(60)) + int(low.tail(60).values.argmin())
        anchors["ath"] = self._compute_vwap(typical, volume, ath_pos)
        anchors["atl"] = self._compute_vwap(typical, volume, atl_pos)

        # ── Анализ позиции цены относительно AVWAPs ──────────────────────────
        above = []
        below = []
        levels = {}

        labels = {
            "session":    "AVWAP сессии",
            "swing_high": "AVWAP от Swing High",
            "swing_low":  "AVWAP от Swing Low",
            "hvn":        "AVWAP от HVN",
            "ath":        "AVWAP от ATH",
            "atl":        "AVWAP от ATL",
        }

        for key, vwap_val in anchors.items():
            if vwap_val is None:
                continue
            levels[key] = round(vwap_val, 4)
            if last_price > vwap_val:
                above.append(labels[key])
            else:
                below.append(labels[key])

        # ── Ближайшие AVWAP уровни (поддержка/сопротивление) ─────────────────
        all_vals = [(k, v) for k, v in levels.items() if v > 0]
        all_vals_sorted = sorted(all_vals, key=lambda x: abs(x[1] - last_price))
        nearest = all_vals_sorted[:3]

        # ── Сигнал ────────────────────────────────────────────────────────────
        score = 0
        signals = []

        # Если цена выше большинства AVWAP → бычий контекст
        if len(above) > len(below):
            score += min(40, len(above) * 10)
            signals.append(f"🟢 Цена выше {len(above)}/{len(anchors)} AVWAP уровней")
        elif len(below) > len(above):
            score -= min(40, len(below) * 10)
            signals.append(f"🔴 Цена ниже {len(below)}/{len(anchors)} AVWAP уровней")

        # Цена прямо у значимого AVWAP (±0.3%) → потенциальный отскок/пробой
        for name, val in nearest[:1]:
            dist_pct = abs(last_price - val) / last_price * 100
            if dist_pct < 0.3:
                if last_price > val:
                    signals.append(f"📍 Цена у {labels.get(name, name)} (${val:.4f}) — поддержка")
                    score += 15
                else:
                    signals.append(f"📍 Цена у {labels.get(name, name)} (${val:.4f}) — сопротивление")
                    score -= 15

        # Swing High AVWAP выше цены = сопротивление
        if "swing_high" in levels and levels["swing_high"] > last_price:
            dist = (levels["swing_high"] - last_price) / last_price * 100
            if dist < 1.0:
                signals.append(f"⚠️ AVWAP Swing High ${levels['swing_high']:.4f} — зона сопротивления")

        # Swing Low AVWAP ниже цены = поддержка
        if "swing_low" in levels and levels["swing_low"] < last_price:
            dist = (last_price - levels["swing_low"]) / last_price * 100
            if dist < 1.0:
                signals.append(f"✅ AVWAP Swing Low ${levels['swing_low']:.4f} — зона поддержки")

        score = max(-100, min(100, score))
        signal = "bullish" if score > 15 else ("bearish" if score < -15 else "neutral")

        return {
            "signal":        signal,
            "score":         score,
            "levels":        levels,
            "above_count":   len(above),
            "below_count":   len(below),
            "above_list":    above,
            "below_list":    below,
            "nearest":       [(labels.get(n, n), round(v, 4)) for n, v in nearest],
            "signals":       signals,
        }

    # ── VWAP от якоря ─────────────────────────────────────────────────────────
    def _compute_vwap(self, typical: pd.Series, volume: pd.Series, from_idx: int) -> float | None:
        try:
            t = typical.iloc[from_idx:]
            v = volume.iloc[from_idx:]
            if len(t) == 0 or v.sum() == 0:
                return None
            return float((t * v).sum() / v.sum())
        except Exception:
            return None

    # ── Swing High / Low ──────────────────────────────────────────────────────
    def _find_swing_high(self, high: pd.Series, lookback: int = 50) -> int | None:
        """Последний значимый Swing High"""
        h = high.tail(lookback).values
        n = len(h)
        for i in range(n - 3, 2, -1):
            if h[i] > h[i-1] and h[i] > h[i-2] and h[i] > h[i+1] and h[i] > h[i+2]:
                return len(high) - lookback + i
        return None

    def _find_swing_low(self, low: pd.Series, lookback: int = 50) -> int | None:
        """Последний значимый Swing Low"""
        l = low.tail(lookback).values
        n = len(l)
        for i in range(n - 3, 2, -1):
            if l[i] < l[i-1] and l[i] < l[i-2] and l[i] < l[i+1] and l[i] < l[i+2]:
                return len(low) - lookback + i
        return None

    def _find_hvn(self, volume: pd.Series, lookback: int = 50) -> int | None:
        """High Volume Node — свеча с максимальным объёмом"""
        tail = volume.tail(lookback)
        if tail.empty:
            return None
        pos = int(tail.values.argmax())
        return len(volume) - lookback + pos

    def _empty(self) -> dict:
        return {
            "signal": "neutral", "score": 0, "levels": {},
            "above_count": 0, "below_count": 0,
            "above_list": [], "below_list": [],
            "nearest": [], "signals": [],
        }
