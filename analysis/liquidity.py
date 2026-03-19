"""
analysis/liquidity.py — Liquidity Sweep Detection
────────────────────────────────────────────────────
Концепция: Крупные игроки (маркет-мейкеры, банки) намеренно двигают цену
за уровни скопления стоп-лоссов, чтобы набрать/сбросить позиции.

Что определяем:
  1. Equal Highs / Equal Lows — зоны скопления стопов
  2. Sweep + Rejection — пробой с возвратом (ложный пробой)
  3. Inducement — приманка перед настоящим движением
  4. Buy-side / Sell-side Liquidity пулы
  5. Stop Hunt паттерн
"""

import pandas as pd
import numpy as np
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class LiquidityLevel:
    price: float
    type: str          # "buy_side" | "sell_side"
    strength: int      # 1-3 (количество касаний)
    swept: bool = False
    sweep_candle: Optional[int] = None


@dataclass
class LiquiditySweep:
    direction: str     # "bullish_sweep" (swept sell-side) | "bearish_sweep" (swept buy-side)
    swept_level: float
    sweep_low: float
    sweep_high: float
    rejection_strength: float   # насколько быстро цена вернулась
    candle_index: int
    confirmed: bool    # подтверждён ли закрытием свечи


class LiquidityAnalyzer:

    def analyze(self, df: pd.DataFrame) -> dict:
        """
        Полный анализ ликвидности.
        Возвращает уровни, свипы, сигнал и описание.
        """
        if len(df) < 30:
            return self._empty()

        close = df["Close"].values
        high  = df["High"].values
        low   = df["Low"].values
        vol   = df["Volume"].values

        # ── 1. Найти Equal Highs / Equal Lows ───────────────────────────────
        eq_highs = self._find_equal_levels(high, mode="high")
        eq_lows  = self._find_equal_levels(low,  mode="low")

        # ── 2. Найти свипы (ложные пробои) ──────────────────────────────────
        sweeps = self._find_sweeps(high, low, close, vol, eq_highs, eq_lows)

        # ── 3. Найти зоны ликвидности ────────────────────────────────────────
        buy_side  = self._buy_side_liquidity(high, low, close)
        sell_side = self._sell_side_liquidity(high, low, close)

        # ── 4. Проверить inducement ───────────────────────────────────────────
        inducement = self._detect_inducement(high, low, close)

        # ── 5. Итоговый сигнал ────────────────────────────────────────────────
        signal, description, score = self._evaluate(sweeps, buy_side, sell_side, inducement, close)

        return {
            "signal":       signal,          # "bullish" | "bearish" | "neutral"
            "score":        score,           # -100..+100
            "description":  description,
            "sweeps":       [self._sweep_to_dict(s) for s in sweeps[-3:]],
            "buy_side_liq":  [round(p, 4) for p in buy_side[:3]],
            "sell_side_liq": [round(p, 4) for p in sell_side[:3]],
            "inducement":   inducement,
            "eq_highs":     [round(p, 4) for p in eq_highs[:3]],
            "eq_lows":      [round(p, 4) for p in eq_lows[:3]],
        }

    # ── Equal Highs / Equal Lows ──────────────────────────────────────────────
    def _find_equal_levels(self, prices: np.ndarray, mode: str, tolerance: float = 0.002) -> list:
        """Находит уровни где цена дважды+  касалась одного значения (±0.2%)"""
        n = len(prices)
        levels = []
        used = set()

        # Ищем локальные экстремумы
        extremes = []
        for i in range(2, n - 2):
            if mode == "high":
                if prices[i] >= prices[i-1] and prices[i] >= prices[i+1]:
                    extremes.append((i, prices[i]))
            else:
                if prices[i] <= prices[i-1] and prices[i] <= prices[i+1]:
                    extremes.append((i, prices[i]))

        # Группируем близкие уровни
        for i, (idx1, p1) in enumerate(extremes):
            if idx1 in used:
                continue
            group = [p1]
            for j, (idx2, p2) in enumerate(extremes[i+1:], i+1):
                if idx2 in used:
                    continue
                if abs(p2 - p1) / (p1 + 1e-10) <= tolerance:
                    group.append(p2)
                    used.add(idx2)
            if len(group) >= 2:
                levels.append(np.mean(group))
                used.add(idx1)

        return sorted(levels, reverse=(mode == "high"))

    # ── Liquidity Sweeps ──────────────────────────────────────────────────────
    def _find_sweeps(self, high, low, close, vol, eq_highs, eq_lows) -> list:
        sweeps = []
        n = len(close)

        # Свип вверх (bearish sweep buy-side): пробой equal highs + возврат ниже
        for level in eq_highs:
            for i in range(3, n):
                if (high[i] > level * 1.001           # пробой уровня
                        and close[i] < level           # закрытие ниже (rejection)
                        and high[i] - close[i] > (high[i] - low[i]) * 0.4):  # длинный верхний фитиль
                    rejection = (high[i] - close[i]) / (high[i] - low[i] + 1e-10)
                    sweeps.append(LiquiditySweep(
                        direction="bearish_sweep",
                        swept_level=level,
                        sweep_low=low[i],
                        sweep_high=high[i],
                        rejection_strength=rejection,
                        candle_index=i,
                        confirmed=close[i] < level
                    ))

        # Свип вниз (bullish sweep sell-side): пробой equal lows + возврат выше
        for level in eq_lows:
            for i in range(3, n):
                if (low[i] < level * 0.999             # пробой ниже
                        and close[i] > level           # закрытие выше (rejection)
                        and close[i] - low[i] > (high[i] - low[i]) * 0.4):
                    rejection = (close[i] - low[i]) / (high[i] - low[i] + 1e-10)
                    sweeps.append(LiquiditySweep(
                        direction="bullish_sweep",
                        swept_level=level,
                        sweep_low=low[i],
                        sweep_high=high[i],
                        rejection_strength=rejection,
                        candle_index=i,
                        confirmed=close[i] > level
                    ))

        # Берём только свипы из последних 20 свечей
        recent_cutoff = len(close) - 20
        sweeps = [s for s in sweeps if s.candle_index >= recent_cutoff]
        # Сортируем по позиции (последние сначала)
        return sorted(sweeps, key=lambda s: s.candle_index, reverse=True)

    # ── Buy/Sell Side Liquidity ───────────────────────────────────────────────
    def _buy_side_liquidity(self, high, low, close) -> list:
        """Buy-side liquidity = выше недавних хаёв (там сидят стопы шортистов и лимитники)"""
        recent = high[-30:]
        # Находим значимые хаи
        levels = []
        for i in range(1, len(recent) - 1):
            if recent[i] >= recent[i-1] and recent[i] >= recent[i+1]:
                levels.append(float(recent[i]))
        return sorted(set(levels), reverse=True)[:5]

    def _sell_side_liquidity(self, high, low, close) -> list:
        """Sell-side liquidity = ниже недавних лоёв (там стопы лонгистов)"""
        recent = low[-30:]
        levels = []
        for i in range(1, len(recent) - 1):
            if recent[i] <= recent[i-1] and recent[i] <= recent[i+1]:
                levels.append(float(recent[i]))
        return sorted(set(levels))[:5]

    # ── Inducement ────────────────────────────────────────────────────────────
    def _detect_inducement(self, high, low, close) -> str:
        """
        Inducement = небольшой ложный пробой перед настоящим движением.
        Часто предшествует MSB (Market Structure Break).
        """
        n = len(close)
        if n < 10:
            return ""

        # Смотрим последние 5 свечей
        recent_high = max(high[-10:-2])
        recent_low  = min(low[-10:-2])
        last_close  = close[-1]
        last_high   = high[-1]
        last_low    = low[-1]

        # Цена недавно пробила хай, но вернулась → приманка для лонгистов
        if last_high > recent_high and last_close < recent_high:
            return "⚠️ Inducement вверх: ложный пробой хая, возможный разворот вниз"

        # Цена недавно пробила лоу, но вернулась → приманка для шортистов
        if last_low < recent_low and last_close > recent_low:
            return "⚠️ Inducement вниз: ложный пробой лоя, возможный разворот вверх"

        return ""

    # ── Оценка ────────────────────────────────────────────────────────────────
    def _evaluate(self, sweeps, buy_side, sell_side, inducement, close) -> tuple:
        score = 0
        signals = []
        last_price = float(close[-1])

        for sweep in sweeps[:2]:  # последние 2 свипа
            if not sweep.confirmed:
                continue
            recency = max(0, 1 - (len(close) - 1 - sweep.candle_index) / 20)
            weight = recency * sweep.rejection_strength * 60

            if sweep.direction == "bullish_sweep":
                score += weight
                signals.append(f"🟢 Bullish sweep на ${sweep.swept_level:.4f} (отбой {sweep.rejection_strength:.0%})")
            else:
                score -= weight
                signals.append(f"🔴 Bearish sweep на ${sweep.swept_level:.4f} (отбой {sweep.rejection_strength:.0%})")

        # Цена близко к sell-side ликвидности → риск свипа вниз
        if sell_side:
            nearest_ssl = min(sell_side, key=lambda x: abs(x - last_price))
            dist = (last_price - nearest_ssl) / last_price
            if dist < 0.005:
                score -= 15
                signals.append(f"⚠️ Цена у sell-side ликвидности ${nearest_ssl:.4f}")

        # Цена близко к buy-side ликвидности → риск свипа вверх
        if buy_side:
            nearest_bsl = min(buy_side, key=lambda x: abs(x - last_price))
            dist = (nearest_bsl - last_price) / last_price
            if dist < 0.005:
                score += 15
                signals.append(f"⚠️ Цена у buy-side ликвидности ${nearest_bsl:.4f}")

        if inducement:
            signals.append(inducement)

        score = max(-100, min(100, int(score)))
        signal = "bullish" if score > 20 else ("bearish" if score < -20 else "neutral")
        description = " | ".join(signals) if signals else "Значимых свипов не обнаружено"

        return signal, description, score

    def _sweep_to_dict(self, s: LiquiditySweep) -> dict:
        return {
            "direction":  s.direction,
            "level":      round(s.swept_level, 4),
            "rejection":  round(s.rejection_strength, 2),
            "confirmed":  s.confirmed,
        }

    def _empty(self) -> dict:
        return {
            "signal": "neutral", "score": 0,
            "description": "Недостаточно данных",
            "sweeps": [], "buy_side_liq": [], "sell_side_liq": [],
            "inducement": "", "eq_highs": [], "eq_lows": [],
        }
