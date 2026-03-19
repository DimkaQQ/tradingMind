"""
analysis/technical.py — 15+ технических индикаторов
RSI, MACD, EMA, Bollinger, VWAP, ADX, OBV, ATR, Stoch RSI, CCI, Williams %R
"""

import logging
from typing import Optional
import pandas as pd
import numpy as np
import yfinance as yf

logger = logging.getLogger(__name__)

TIMEFRAME_CFG = {
    "15m": ("5d",  "15m"),
    "1h":  ("30d", "1h"),
    "4h":  ("90d", "1h"),   # yfinance нет 4h → берём 1h за 90д и ресемплим
}


class TechnicalAnalyzer:

    def fetch_and_calc(self, symbol: str, timeframe: str) -> Optional[dict]:
        df = self._fetch(symbol, timeframe)
        if df is None or len(df) < 60:
            raise ValueError(f"Мало данных для {symbol} {timeframe}")
        indicators = self._calc(df)
        return {
            "price": float(df["Close"].iloc[-1]),
            "df": df,
            "indicators": indicators,
        }

    def _fetch(self, symbol: str, timeframe: str) -> Optional[pd.DataFrame]:
        try:
            period, interval = TIMEFRAME_CFG.get(timeframe, ("30d", "1h"))
            df = yf.Ticker(symbol).history(period=period, interval=interval)
            if df.empty:
                return None
            df = df.dropna()
            # Ресемплинг в 4h если нужно
            if timeframe == "4h":
                df = df.resample("4h").agg({
                    "Open": "first", "High": "max", "Low": "min",
                    "Close": "last", "Volume": "sum"
                }).dropna()
            return df
        except Exception as e:
            logger.error(f"Fetch {symbol} {timeframe}: {e}")
            return None

    def _calc(self, df: pd.DataFrame) -> dict:
        close = df["Close"]
        high  = df["High"]
        low   = df["Low"]
        vol   = df["Volume"]

        # ── RSI ──────────────────────────────────────────────────────────────
        rsi = self._rsi(close, 14)

        # ── MACD ─────────────────────────────────────────────────────────────
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd  = ema12 - ema26
        signal = macd.ewm(span=9, adjust=False).mean()
        macd_hist = macd - signal

        # ── EMA ──────────────────────────────────────────────────────────────
        ema9  = close.ewm(span=9,  adjust=False).mean()
        ema21 = close.ewm(span=21, adjust=False).mean()
        ema50 = close.ewm(span=50, adjust=False).mean()
        last = float(close.iloc[-1])
        if last > ema9.iloc[-1] > ema21.iloc[-1] > ema50.iloc[-1]:
            ema_trend = "📈 UP"
        elif last < ema9.iloc[-1] < ema21.iloc[-1] < ema50.iloc[-1]:
            ema_trend = "📉 DOWN"
        else:
            ema_trend = "➡️ SIDEWAYS"

        # ── Bollinger Bands ───────────────────────────────────────────────────
        bb_mid = close.rolling(20).mean()
        bb_std = close.rolling(20).std()
        bb_up  = bb_mid + 2 * bb_std
        bb_dn  = bb_mid - 2 * bb_std
        if last >= float(bb_up.iloc[-1]) * 0.995:
            bb_pos = "🔝 upper"
        elif last <= float(bb_dn.iloc[-1]) * 1.005:
            bb_pos = "⬇️ lower"
        else:
            bb_pos = "🎯 middle"

        # ── VWAP ─────────────────────────────────────────────────────────────
        typical = (high + low + close) / 3
        vwap = (typical * vol).cumsum() / vol.cumsum()

        # ── ADX ──────────────────────────────────────────────────────────────
        adx = self._adx(high, low, close, 14)

        # ── OBV ──────────────────────────────────────────────────────────────
        obv = (np.sign(close.diff()) * vol).fillna(0).cumsum()
        obv_slope = float(obv.iloc[-1]) - float(obv.iloc[-5])
        obv_signal = "📈 растёт" if obv_slope > 0 else "📉 падает"

        # ── ATR ───────────────────────────────────────────────────────────────
        atr = self._atr(high, low, close, 14)

        # ── Stochastic RSI ────────────────────────────────────────────────────
        stoch_rsi = self._stoch_rsi(close, 14)

        # ── CCI ───────────────────────────────────────────────────────────────
        cci = self._cci(high, low, close, 20)

        # ── Williams %R ───────────────────────────────────────────────────────
        willr = self._williams_r(high, low, close, 14)

        # ── Volume ────────────────────────────────────────────────────────────
        vol_avg = float(vol.rolling(20).mean().iloc[-1])
        vol_last = float(vol.iloc[-1])
        if vol_last > vol_avg * 1.5:
            vol_signal = "🔥 очень высокий"
        elif vol_last > vol_avg * 1.2:
            vol_signal = "📊 высокий"
        elif vol_last < vol_avg * 0.6:
            vol_signal = "📉 низкий"
        else:
            vol_signal = "✅ нормальный"

        # ── Поддержка / Сопротивление (volume-weighted) ────────────────────
        recent = df.tail(50)
        support    = float(recent["Low"].min())
        resistance = float(recent["High"].max())
        # Pivot points
        prev = df.iloc[-2]
        pivot = (float(prev["High"]) + float(prev["Low"]) + float(prev["Close"])) / 3
        r1 = 2 * pivot - float(prev["Low"])
        s1 = 2 * pivot - float(prev["High"])

        return {
            "rsi":          float(rsi.iloc[-1]),
            "macd_hist":    float(macd_hist.iloc[-1]),
            "macd_signal":  float(signal.iloc[-1]),
            "ema9":         float(ema9.iloc[-1]),
            "ema21":        float(ema21.iloc[-1]),
            "ema50":        float(ema50.iloc[-1]),
            "ema_trend":    ema_trend,
            "bb_upper":     float(bb_up.iloc[-1]),
            "bb_lower":     float(bb_dn.iloc[-1]),
            "bb_position":  bb_pos,
            "vwap":         float(vwap.iloc[-1]),
            "adx":          float(adx.iloc[-1]),
            "obv_signal":   obv_signal,
            "atr":          float(atr.iloc[-1]),
            "stoch_rsi":    float(stoch_rsi),
            "cci":          float(cci.iloc[-1]),
            "williams_r":   float(willr.iloc[-1]),
            "volume_signal": vol_signal,
            "support":      support,
            "resistance":   resistance,
            "pivot":        pivot,
            "r1":           r1,
            "s1":           s1,
        }

    # ── Вспомогательные ──────────────────────────────────────────────────────

    def _rsi(self, s: pd.Series, p: int) -> pd.Series:
        d = s.diff()
        g = d.clip(lower=0).ewm(com=p-1, adjust=False).mean()
        l = (-d.clip(upper=0)).ewm(com=p-1, adjust=False).mean()
        return 100 - 100 / (1 + g / (l + 1e-10))

    def _stoch_rsi(self, s: pd.Series, p: int = 14) -> float:
        rsi = self._rsi(s, p)
        mn = rsi.rolling(p).min()
        mx = rsi.rolling(p).max()
        return float(((rsi - mn) / (mx - mn + 1e-10) * 100).iloc[-1])

    def _atr(self, h, l, c, p=14) -> pd.Series:
        tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
        return tr.ewm(com=p-1, adjust=False).mean()

    def _adx(self, h, l, c, p=14) -> pd.Series:
        tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
        dm_plus  = ((h - h.shift()) > (l.shift() - l)).astype(float) * (h - h.shift()).clip(lower=0)
        dm_minus = ((l.shift() - l) > (h - h.shift())).astype(float) * (l.shift() - l).clip(lower=0)
        atr = tr.ewm(com=p-1, adjust=False).mean()
        di_plus  = 100 * dm_plus.ewm(com=p-1, adjust=False).mean() / (atr + 1e-10)
        di_minus = 100 * dm_minus.ewm(com=p-1, adjust=False).mean() / (atr + 1e-10)
        dx = (di_plus - di_minus).abs() / (di_plus + di_minus + 1e-10) * 100
        return dx.ewm(com=p-1, adjust=False).mean()

    def _cci(self, h, l, c, p=20) -> pd.Series:
        tp = (h + l + c) / 3
        mad = tp.rolling(p).apply(lambda x: np.mean(np.abs(x - x.mean())), raw=True)
        return (tp - tp.rolling(p).mean()) / (0.015 * mad + 1e-10)

    def _williams_r(self, h, l, c, p=14) -> pd.Series:
        hh = h.rolling(p).max()
        ll = l.rolling(p).min()
        return -100 * (hh - c) / (hh - ll + 1e-10)

    def score(self, ind: dict) -> tuple[int, list[str]]:
        """Скоринг всех индикаторов → (-100, +100)"""
        score = 0
        signals = []

        rsi = ind.get("rsi", 50)
        if rsi < 25:   score += 30; signals.append(f"RSI={rsi:.0f} экстремально перепродан")
        elif rsi < 35: score += 18; signals.append(f"RSI={rsi:.0f} перепродан")
        elif rsi < 45: score += 8
        elif rsi > 75: score -= 30; signals.append(f"RSI={rsi:.0f} экстремально перекуплен")
        elif rsi > 65: score -= 18; signals.append(f"RSI={rsi:.0f} перекуплен")
        elif rsi > 55: score -= 8

        if ind.get("macd_hist", 0) > 0: score += 15; signals.append("MACD бычий")
        else: score -= 15; signals.append("MACD медвежий")

        ema_t = ind.get("ema_trend", "")
        if "UP" in ema_t:   score += 20; signals.append("EMA восходящий тренд")
        elif "DOWN" in ema_t: score -= 20; signals.append("EMA нисходящий тренд")

        bb = ind.get("bb_position", "")
        if "lower" in bb: score += 12; signals.append("Цена у нижней BB")
        elif "upper" in bb: score -= 12; signals.append("Цена у верхней BB")

        adx = ind.get("adx", 0)
        if adx > 40: score = int(score * 1.2); signals.append(f"ADX={adx:.0f} очень сильный тренд")
        elif adx > 25: score = int(score * 1.1); signals.append(f"ADX={adx:.0f} сильный тренд")
        elif adx < 15: score = int(score * 0.8); signals.append(f"ADX={adx:.0f} слабый тренд/боковик")

        vol = ind.get("volume_signal", "")
        if "высокий" in vol or "очень" in vol: score = int(score * 1.15)

        stoch = ind.get("stoch_rsi", 50)
        if stoch < 15:   score += 10; signals.append(f"Stoch RSI={stoch:.0f} перепродан")
        elif stoch > 85: score -= 10; signals.append(f"Stoch RSI={stoch:.0f} перекуплен")

        cci = ind.get("cci", 0)
        if cci < -150: score += 8; signals.append("CCI перепродан")
        elif cci > 150: score -= 8; signals.append("CCI перекуплен")

        wr = ind.get("williams_r", -50)
        if wr < -85: score += 7; signals.append("Williams %R перепродан")
        elif wr > -15: score -= 7; signals.append("Williams %R перекуплен")

        obv = ind.get("obv_signal", "")
        if "растёт" in obv: score += 5
        else: score -= 5

        return max(-100, min(100, score)), signals
