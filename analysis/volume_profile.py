"""
analysis/volume_profile.py — Volume Profile
────────────────────────────────────────────
Концепция: Распределение объёма по ценовым уровням показывает
где именно происходили крупные сделки.

Ключевые зоны:
  POC (Point of Control)  — уровень с максимальным объёмом
  VAH (Value Area High)   — верхняя граница зоны стоимости (70% объёма)
  VAL (Value Area Low)    — нижняя граница зоны стоимости
  HVN (High Volume Node)  — зоны с высоким объёмом = магниты и поддержки
  LVN (Low Volume Node)   — зоны с малым объёмом = цена проходит быстро

Торговая логика:
  • Цена у POC  → может консолидироваться
  • Цена < VAL  → потенциальное движение к VAL (возврат к стоимости)
  • Цена > VAH  → потенциальное движение к VAH при откате
  • LVN выше    → цена может быстро достичь следующего HVN
"""

import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)


class VolumeProfileAnalyzer:

    def __init__(self, bins: int = 50):
        self.bins = bins  # количество ценовых уровней

    def analyze(self, df: pd.DataFrame) -> dict:
        if len(df) < 20:
            return self._empty()

        close  = df["Close"]
        high   = df["High"]
        low    = df["Low"]
        volume = df["Volume"]
        last_price = float(close.iloc[-1])

        # ── Строим Volume Profile ─────────────────────────────────────────────
        profile = self._build_profile(high, low, volume)
        if profile is None:
            return self._empty()

        price_levels, vol_by_level = profile

        # ── POC ───────────────────────────────────────────────────────────────
        poc_idx = int(np.argmax(vol_by_level))
        poc     = float(price_levels[poc_idx])

        # ── Value Area (70% объёма вокруг POC) ───────────────────────────────
        vah, val = self._value_area(price_levels, vol_by_level, poc_idx, target_pct=0.70)

        # ── HVN / LVN ─────────────────────────────────────────────────────────
        hvn_levels = self._find_hvn(price_levels, vol_by_level, top_n=5)
        lvn_levels = self._find_lvn(price_levels, vol_by_level, top_n=5)

        # ── Контекст цены ─────────────────────────────────────────────────────
        in_value_area = val <= last_price <= vah
        above_poc     = last_price > poc
        above_vah     = last_price > vah
        below_val     = last_price < val

        # Ближайший HVN выше и ниже
        hvn_above = [h for h in hvn_levels if h > last_price]
        hvn_below = [h for h in hvn_levels if h < last_price]
        nearest_hvn_above = min(hvn_above) if hvn_above else None
        nearest_hvn_below = max(hvn_below) if hvn_below else None

        # Ближайший LVN (зона быстрого прохода)
        lvn_above = [l for l in lvn_levels if l > last_price]
        lvn_below = [l for l in lvn_levels if l < last_price]
        nearest_lvn_above = min(lvn_above) if lvn_above else None

        # ── Тренд POC (смотрим как двигался POC за последние периоды) ─────────
        poc_trend = self._poc_trend(high, low, volume)

        # ── Сигнал ────────────────────────────────────────────────────────────
        score, signal, signals = self._evaluate(
            last_price, poc, vah, val,
            in_value_area, above_poc, above_vah, below_val,
            nearest_hvn_above, nearest_hvn_below, nearest_lvn_above,
            poc_trend
        )

        return {
            "signal":          signal,
            "score":           score,
            "poc":             round(poc, 4),
            "vah":             round(vah, 4),
            "val":             round(val, 4),
            "hvn_levels":      [round(h, 4) for h in hvn_levels],
            "lvn_levels":      [round(l, 4) for l in lvn_levels],
            "in_value_area":   in_value_area,
            "above_poc":       above_poc,
            "above_vah":       above_vah,
            "below_val":       below_val,
            "poc_trend":       poc_trend,
            "nearest_hvn_above": round(nearest_hvn_above, 4) if nearest_hvn_above else None,
            "nearest_hvn_below": round(nearest_hvn_below, 4) if nearest_hvn_below else None,
            "nearest_lvn_above": round(nearest_lvn_above, 4) if nearest_lvn_above else None,
            "signals":         signals,
        }

    # ── Построение профиля ────────────────────────────────────────────────────
    def _build_profile(self, high, low, volume):
        """Распределяет объём каждой свечи по ценовым уровням (TPO-like)"""
        try:
            price_min = float(low.min())
            price_max = float(high.max())
            if price_max <= price_min:
                return None

            price_levels = np.linspace(price_min, price_max, self.bins)
            vol_by_level = np.zeros(self.bins)
            bin_size = (price_max - price_min) / self.bins

            for i in range(len(high)):
                h = float(high.iloc[i])
                l = float(low.iloc[i])
                v = float(volume.iloc[i])
                candle_range = h - l
                if candle_range <= 0:
                    # Дожи — весь объём на один уровень
                    idx = int((h - price_min) / (price_max - price_min) * (self.bins - 1))
                    vol_by_level[min(idx, self.bins - 1)] += v
                    continue
                # Равномерно распределяем объём по уровням свечи
                for j, level in enumerate(price_levels):
                    if l <= level <= h:
                        vol_by_level[j] += v * (bin_size / candle_range)

            return price_levels, vol_by_level
        except Exception as e:
            logger.error(f"Profile build error: {e}")
            return None

    # ── Value Area ────────────────────────────────────────────────────────────
    def _value_area(self, levels, vols, poc_idx, target_pct=0.70):
        total_vol = vols.sum()
        target = total_vol * target_pct

        low_idx  = poc_idx
        high_idx = poc_idx
        accumulated = vols[poc_idx]

        while accumulated < target:
            can_go_up   = high_idx < len(vols) - 1
            can_go_down = low_idx > 0

            if can_go_up and can_go_down:
                if vols[high_idx + 1] >= vols[low_idx - 1]:
                    high_idx += 1
                    accumulated += vols[high_idx]
                else:
                    low_idx -= 1
                    accumulated += vols[low_idx]
            elif can_go_up:
                high_idx += 1
                accumulated += vols[high_idx]
            elif can_go_down:
                low_idx -= 1
                accumulated += vols[low_idx]
            else:
                break

        return float(levels[high_idx]), float(levels[low_idx])

    # ── HVN / LVN ─────────────────────────────────────────────────────────────
    def _find_hvn(self, levels, vols, top_n=5) -> list:
        """High Volume Nodes — зоны притяжения"""
        threshold = np.percentile(vols, 75)
        hvn = []
        for i in range(1, len(vols) - 1):
            if vols[i] >= threshold and vols[i] >= vols[i-1] and vols[i] >= vols[i+1]:
                hvn.append(float(levels[i]))
        return sorted(hvn, key=lambda x: vols[np.argmin(np.abs(levels - x))], reverse=True)[:top_n]

    def _find_lvn(self, levels, vols, top_n=5) -> list:
        """Low Volume Nodes — зоны быстрого прохода"""
        threshold = np.percentile(vols, 25)
        lvn = []
        for i in range(1, len(vols) - 1):
            if vols[i] <= threshold and vols[i] <= vols[i-1] and vols[i] <= vols[i+1]:
                lvn.append(float(levels[i]))
        return sorted(lvn)[:top_n]

    # ── POC Trend ─────────────────────────────────────────────────────────────
    def _poc_trend(self, high, low, volume) -> str:
        """Смотрим куда движется POC: вверх = накопление, вниз = распределение"""
        try:
            mid_point = len(high) // 2
            # POC первой половины
            profile1 = self._build_profile(high.iloc[:mid_point], low.iloc[:mid_point], volume.iloc[:mid_point])
            # POC второй половины
            profile2 = self._build_profile(high.iloc[mid_point:], low.iloc[mid_point:], volume.iloc[mid_point:])
            if profile1 and profile2:
                poc1 = float(profile1[0][int(np.argmax(profile1[1]))])
                poc2 = float(profile2[0][int(np.argmax(profile2[1]))])
                if poc2 > poc1 * 1.002:
                    return "📈 POC движется вверх (накопление)"
                elif poc2 < poc1 * 0.998:
                    return "📉 POC движется вниз (распределение)"
        except Exception:
            pass
        return "➡️ POC стабилен"

    # ── Оценка ────────────────────────────────────────────────────────────────
    def _evaluate(self, price, poc, vah, val, in_va, above_poc,
                  above_vah, below_val, hvn_above, hvn_below, lvn_above, poc_trend) -> tuple:
        score = 0
        signals = []
        price_pct = (price - poc) / poc * 100

        # Позиция относительно Value Area
        if above_vah:
            score -= 20
            signals.append(f"🔴 Цена выше VAH ${vah:.4f} — зона распределения/перекупленность")
        elif below_val:
            score += 20
            signals.append(f"🟢 Цена ниже VAL ${val:.4f} — потенциальный возврат к стоимости")
        elif in_va:
            # Внутри VA: смотрим на POC
            if above_poc:
                score += 5
                signals.append(f"📊 Цена в Value Area, выше POC ${poc:.4f}")
            else:
                score -= 5
                signals.append(f"📊 Цена в Value Area, ниже POC ${poc:.4f}")

        # Близость к POC
        if abs(price_pct) < 0.3:
            signals.append(f"🎯 Цена у POC ${poc:.4f} — зона максимального объёма (магнит)")

        # LVN выше — цена может быстро вырасти до следующего HVN
        if lvn_above and hvn_above:
            if lvn_above < hvn_above:
                score += 10
                signals.append(f"⚡ LVN ${lvn_above:.4f} → HVN ${hvn_above:.4f}: зона быстрого роста")

        # HVN ниже — сильная поддержка
        if hvn_below:
            dist = (price - hvn_below) / price * 100
            if dist < 1.0:
                score += 12
                signals.append(f"🛡 HVN поддержка ${hvn_below:.4f} ({dist:.1f}% ниже)")

        # POC trend
        if "вверх" in poc_trend:
            score += 15
            signals.append(poc_trend)
        elif "вниз" in poc_trend:
            score -= 15
            signals.append(poc_trend)

        score = max(-100, min(100, score))
        signal = "bullish" if score > 15 else ("bearish" if score < -15 else "neutral")
        return score, signal, signals

    def _empty(self):
        return {
            "signal": "neutral", "score": 0,
            "poc": 0, "vah": 0, "val": 0,
            "hvn_levels": [], "lvn_levels": [],
            "in_value_area": False, "above_poc": False,
            "above_vah": False, "below_val": False,
            "poc_trend": "", "nearest_hvn_above": None,
            "nearest_hvn_below": None, "nearest_lvn_above": None,
            "signals": [],
        }
