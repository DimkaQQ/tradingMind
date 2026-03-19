"""
analysis/multi_timeframe.py — MTF консенсус
analysis/correlation.py — корреляция активов
"""

# ─────────────────────────────────────────────────────────
# multi_timeframe.py
# ─────────────────────────────────────────────────────────

class MultiTimeframeAnalyzer:

    def get_bias(self, indicators: dict) -> dict:
        """Определяет bias (bullish/bearish/neutral) для одного таймфрейма"""
        score = 0

        rsi = indicators.get("rsi", 50)
        if rsi < 40:   score += 1
        elif rsi > 60: score -= 1

        if indicators.get("macd_hist", 0) > 0: score += 1
        else: score -= 1

        ema = indicators.get("ema_trend", "")
        if "UP" in ema:   score += 2
        elif "DOWN" in ema: score -= 2

        adx = indicators.get("adx", 0)
        weight = 1.2 if adx > 25 else 0.8

        final = score * weight
        if final > 1.5:    bias = "bullish"
        elif final < -1.5: bias = "bearish"
        else:              bias = "neutral"

        return {
            "bias": bias,
            "score": round(final, 2),
            "rsi": rsi,
            "macd_hist": indicators.get("macd_hist", 0),
            "ema_trend": ema,
            "adx": adx,
        }

    def consensus_score(self, mtf: dict) -> int:
        """
        MTF консенсус → скор (-100..+100)
        4h имеет больший вес (тренд), 15m меньший (шум)
        """
        weights = {"4h": 0.5, "1h": 0.35, "15m": 0.15}
        total = 0
        for tf, data in mtf.items():
            if not data:
                continue
            w = weights.get(tf, 0.3)
            s = data.get("score", 0)
            total += s * w

        # Нормализуем в -100..+100
        return max(-100, min(100, int(total * 25)))


# ─────────────────────────────────────────────────────────
# correlation.py
# ─────────────────────────────────────────────────────────

class CorrelationAnalyzer:
    """
    Анализирует влияние BTC на другие активы.
    Если BTC доминанс растёт → капитал уходит из альтов в BTC.
    Если доминанс падает → альты растут.
    """

    # Примерные исторические корреляции с BTC
    CORRELATIONS = {
        "ETH-USD":   0.92,
        "SOL-USD":   0.88,
        "BNB-USD":   0.85,
        "XRP-USD":   0.78,
        "AVAX-USD":  0.87,
        "MATIC-USD": 0.85,
        "LINK-USD":  0.80,
    }

    def btc_impact(self, symbol: str, btc_dominance: float) -> str:
        corr = self.CORRELATIONS.get(symbol, 0.75)

        if btc_dominance == 0:
            return ""

        if btc_dominance > 55:
            return (
                f"BTC доминанс высокий ({btc_dominance:.1f}%) — капитал концентрируется в BTC. "
                f"Для {symbol} (корреляция {corr:.0%}) это негативный сигнал в краткосроке."
            )
        elif btc_dominance < 45:
            return (
                f"BTC доминанс низкий ({btc_dominance:.1f}%) — альт-сезон. "
                f"Для {symbol} (корреляция {corr:.0%}) это позитивный фон."
            )
        else:
            return (
                f"BTC доминанс нейтральный ({btc_dominance:.1f}%). "
                f"Корреляция {symbol} с BTC: {corr:.0%}."
            )
