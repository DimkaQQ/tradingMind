"""
analysis/patterns.py — Паттерны японских свечей
Hammer, Shooting Star, Doji, Engulfing, Morning/Evening Star,
Harami, Marubozu, Three White Soldiers / Three Black Crows
"""

import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)


class CandlePatterns:

    def detect(self, df: pd.DataFrame) -> list[str]:
        """Возвращает список обнаруженных паттернов на последних свечах"""
        if len(df) < 5:
            return []

        patterns = []
        o = df["Open"].values
        h = df["High"].values
        l = df["Low"].values
        c = df["Close"].values

        # Анализируем последние 3 свечи
        i = len(c) - 1

        body = abs(c[i] - o[i])
        full_range = h[i] - l[i]
        upper_wick = h[i] - max(c[i], o[i])
        lower_wick = min(c[i], o[i]) - l[i]
        avg_body = np.mean([abs(c[j] - o[j]) for j in range(i-10, i)])

        # ── Одиночные паттерны ────────────────────────────────────────────

        # Doji
        if body <= full_range * 0.1 and full_range > 0:
            patterns.append("⚖️ Doji (неопределённость)")

        # Hammer (бычий разворот внизу)
        if (lower_wick >= body * 2
                and upper_wick <= body * 0.3
                and body > 0
                and c[i] > o[i]):
            patterns.append("🔨 Hammer (бычий разворот)")

        # Inverted Hammer
        if (upper_wick >= body * 2
                and lower_wick <= body * 0.3
                and body > 0):
            patterns.append("🔃 Inverted Hammer")

        # Shooting Star (медвежий)
        if (upper_wick >= body * 2
                and lower_wick <= body * 0.3
                and body > 0
                and c[i] < o[i]):
            patterns.append("💫 Shooting Star (медвежий разворот)")

        # Marubozu бычий (сильный импульс вверх)
        if (c[i] > o[i]
                and upper_wick <= body * 0.05
                and lower_wick <= body * 0.05
                and body > avg_body * 1.5):
            patterns.append("🟩 Bullish Marubozu (сильный бычий импульс)")

        # Marubozu медвежий
        if (c[i] < o[i]
                and upper_wick <= body * 0.05
                and lower_wick <= body * 0.05
                and body > avg_body * 1.5):
            patterns.append("🟥 Bearish Marubozu (сильный медвежий импульс)")

        # ── Двойные паттерны ─────────────────────────────────────────────
        if i >= 1:
            body_prev = abs(c[i-1] - o[i-1])

            # Bullish Engulfing
            if (c[i-1] < o[i-1]                  # предыдущая медвежья
                    and c[i] > o[i]               # текущая бычья
                    and o[i] <= c[i-1]            # открытие ниже закрытия пред.
                    and c[i] >= o[i-1]            # закрытие выше открытия пред.
                    and body > body_prev):
                patterns.append("🟢 Bullish Engulfing (сильный бычий сигнал)")

            # Bearish Engulfing
            if (c[i-1] > o[i-1]
                    and c[i] < o[i]
                    and o[i] >= c[i-1]
                    and c[i] <= o[i-1]
                    and body > body_prev):
                patterns.append("🔴 Bearish Engulfing (сильный медвежий сигнал)")

            # Bullish Harami
            if (c[i-1] < o[i-1]
                    and c[i] > o[i]
                    and o[i] > c[i-1]
                    and c[i] < o[i-1]
                    and body < body_prev * 0.5):
                patterns.append("📊 Bullish Harami")

            # Bearish Harami
            if (c[i-1] > o[i-1]
                    and c[i] < o[i]
                    and o[i] < c[i-1]
                    and c[i] > o[i-1]
                    and body < body_prev * 0.5):
                patterns.append("📊 Bearish Harami")

            # Tweezer Bottoms (бычий)
            if (abs(l[i] - l[i-1]) < full_range * 0.02
                    and c[i-1] < o[i-1]
                    and c[i] > o[i]):
                patterns.append("🔧 Tweezer Bottoms (бычий)")

            # Tweezer Tops (медвежий)
            if (abs(h[i] - h[i-1]) < full_range * 0.02
                    and c[i-1] > o[i-1]
                    and c[i] < o[i]):
                patterns.append("🔧 Tweezer Tops (медвежий)")

        # ── Тройные паттерны ─────────────────────────────────────────────
        if i >= 2:
            # Morning Star (бычий разворот)
            body1 = abs(c[i-2] - o[i-2])
            body2 = abs(c[i-1] - o[i-1])
            if (c[i-2] < o[i-2]           # большая медвежья
                    and body2 < body1 * 0.3  # маленькое тело
                    and c[i] > o[i]          # большая бычья
                    and c[i] > (o[i-2] + c[i-2]) / 2):
                patterns.append("🌅 Morning Star (разворот вверх)")

            # Evening Star (медвежий разворот)
            if (c[i-2] > o[i-2]
                    and body2 < body1 * 0.3
                    and c[i] < o[i]
                    and c[i] < (o[i-2] + c[i-2]) / 2):
                patterns.append("🌇 Evening Star (разворот вниз)")

            # Three White Soldiers (бычий)
            if (all(c[j] > o[j] for j in [i-2, i-1, i])
                    and c[i] > c[i-1] > c[i-2]
                    and all(abs(c[j]-o[j]) > avg_body for j in [i-2, i-1, i])):
                patterns.append("🪖 Three White Soldiers (сильный бычий)")

            # Three Black Crows (медвежий)
            if (all(c[j] < o[j] for j in [i-2, i-1, i])
                    and c[i] < c[i-1] < c[i-2]
                    and all(abs(c[j]-o[j]) > avg_body for j in [i-2, i-1, i])):
                patterns.append("🦅 Three Black Crows (сильный медвежий)")

        return patterns[:4]  # максимум 4 паттерна в ответе

    def score(self, patterns: list[str]) -> int:
        """Конвертирует паттерны в числовой скор"""
        score = 0
        bullish_patterns = ["Hammer", "Engulfing (сильный бычий", "Marubozu (сильный бычий",
                            "Morning Star", "Three White Soldiers", "Tweezer Bottoms",
                            "Bullish Harami", "Inverted Hammer"]
        bearish_patterns = ["Shooting Star", "Engulfing (сильный медвежий", "Marubozu (сильный медвежий",
                            "Evening Star", "Three Black Crows", "Tweezer Tops",
                            "Bearish Harami"]

        for pat in patterns:
            for b in bullish_patterns:
                if b.lower() in pat.lower():
                    score += 20 if "Three" in pat or "Engulfing" in pat else 12
            for b in bearish_patterns:
                if b.lower() in pat.lower():
                    score -= 20 if "Three" in pat or "Engulfing" in pat else 12

        return max(-60, min(60, score))
