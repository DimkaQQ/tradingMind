"""
core/engine.py — Главный оркестратор v2.1
Интегрирует: TA + MTF + Patterns + Liquidity Sweep +
             Order Flow + Anchored VWAP + Volume Profile + External + Claude AI
"""

import asyncio
import logging
import anthropic

from analysis.technical import TechnicalAnalyzer
from analysis.patterns import CandlePatterns
from analysis.multi_timeframe import MultiTimeframeAnalyzer
from analysis.correlation import CorrelationAnalyzer
from analysis.liquidity import LiquidityAnalyzer
from analysis.order_flow import OrderFlowAnalyzer
from analysis.anchored_vwap import AnchoredVWAPAnalyzer
from analysis.volume_profile import VolumeProfileAnalyzer
from data.external import ExternalDataFetcher

logger = logging.getLogger(__name__)

WEIGHTS = {
    "ta":            0.20,
    "mtf":           0.15,
    "patterns":      0.08,
    "liquidity":     0.22,
    "order_flow":    0.18,
    "anchored_vwap": 0.10,
    "volume_profile":0.07,
}


class TradingEngine:
    def __init__(self, anthropic_key: str):
        self.claude    = anthropic.Anthropic(api_key=anthropic_key)
        self.ta        = TechnicalAnalyzer()
        self.patterns  = CandlePatterns()
        self.mtf       = MultiTimeframeAnalyzer()
        self.corr      = CorrelationAnalyzer()
        self.liquidity = LiquidityAnalyzer()
        self.of        = OrderFlowAnalyzer()
        self.avwap     = AnchoredVWAPAnalyzer()
        self.vp        = VolumeProfileAnalyzer(bins=60)
        self.external  = ExternalDataFetcher()

    async def full_analysis(self, symbol: str) -> dict:
        loop = asyncio.get_event_loop()

        tf_tasks = {
            "15m": loop.run_in_executor(None, self.ta.fetch_and_calc, symbol, "15m"),
            "1h":  loop.run_in_executor(None, self.ta.fetch_and_calc, symbol, "1h"),
            "4h":  loop.run_in_executor(None, self.ta.fetch_and_calc, symbol, "4h"),
        }
        ext_task = self.external.fetch_all(symbol)

        tf_results = {}
        for tf, task in tf_tasks.items():
            try:
                tf_results[tf] = await task
            except Exception as e:
                logger.warning(f"TF {tf} failed for {symbol}: {e}")
                tf_results[tf] = None

        external_data = {}
        try:
            external_data = await ext_task
        except Exception as e:
            logger.warning(f"External data failed: {e}")

        main_data = tf_results.get("1h") or tf_results.get("4h") or tf_results.get("15m")
        if not main_data:
            raise ValueError(f"Нет данных для {symbol}")

        indicators = main_data["indicators"]
        price      = main_data["price"]
        df_1h      = main_data["df"]

        results = await asyncio.gather(
            loop.run_in_executor(None, self.patterns.detect,   df_1h),
            loop.run_in_executor(None, self.liquidity.analyze, df_1h),
            loop.run_in_executor(None, self.of.analyze,        df_1h),
            loop.run_in_executor(None, self.avwap.analyze,     df_1h),
            loop.run_in_executor(None, self.vp.analyze,        df_1h),
            return_exceptions=True
        )

        candle_patterns = results[0] if not isinstance(results[0], Exception) else []
        liq_result      = results[1] if not isinstance(results[1], Exception) else self.liquidity._empty()
        of_result       = results[2] if not isinstance(results[2], Exception) else self.of._empty()
        avwap_result    = results[3] if not isinstance(results[3], Exception) else self.avwap._empty()
        vp_result       = results[4] if not isinstance(results[4], Exception) else self.vp._empty()

        mtf_consensus = {
            tf: self.mtf.get_bias(data["indicators"])
            for tf, data in tf_results.items() if data
        }

        correlation_note = ""
        btc_dom = external_data.get("btc_dominance", 0)
        if btc_dom and symbol not in ("BTC-USD",) and "-USD" in symbol:
            try:
                correlation_note = await loop.run_in_executor(
                    None, self.corr.btc_impact, symbol, btc_dom
                )
            except Exception:
                pass

        ta_score, ta_signals = self.ta.score(indicators)
        mtf_score            = self.mtf.consensus_score(mtf_consensus)
        pattern_score        = self.patterns.score(candle_patterns)
        liq_score            = liq_result.get("score", 0)
        of_score             = of_result.get("score", 0)
        avwap_score          = avwap_result.get("score", 0)
        vp_score             = vp_result.get("score", 0)

        composite_score = int(
            ta_score      * WEIGHTS["ta"]            +
            mtf_score     * WEIGHTS["mtf"]           +
            pattern_score * WEIGHTS["patterns"]      +
            liq_score     * WEIGHTS["liquidity"]     +
            of_score      * WEIGHTS["order_flow"]    +
            avwap_score   * WEIGHTS["anchored_vwap"] +
            vp_score      * WEIGHTS["volume_profile"]
        )

        ai_result = await loop.run_in_executor(
            None, self._ask_claude,
            symbol, price, indicators, mtf_consensus,
            candle_patterns, ta_signals, external_data,
            liq_result, of_result, avwap_result, vp_result,
            correlation_note, composite_score
        )
        direction, confidence = self._parse_ai(ai_result, composite_score)

        return {
            "symbol":          symbol,
            "direction":       direction,
            "confidence":      confidence,
            "price":           price,
            "indicators":      indicators,
            "multi_timeframe": mtf_consensus,
            "patterns":        candle_patterns,
            "external":        external_data,
            "liquidity":       liq_result,
            "order_flow":      of_result,
            "anchored_vwap":   avwap_result,
            "volume_profile":  vp_result,
            "composite_score": composite_score,
            "scores": {
                "ta": ta_score, "mtf": mtf_score, "patterns": pattern_score,
                "liquidity": liq_score, "order_flow": of_score,
                "avwap": avwap_score, "vp": vp_score,
            },
            "ta_signals":  ta_signals,
            "ai_analysis": ai_result,
            "correlation": correlation_note,
        }

    def _ask_claude(self, symbol, price, ind, mtf, patterns, ta_signals,
                    external, liq, of, avwap, vp, correlation, composite_score) -> str:

        mtf_str = "\n".join(
            f"  {tf}: {d.get('bias','?').upper()} | RSI:{d.get('rsi',0):.0f} | ADX:{d.get('adx',0):.0f}"
            for tf, d in mtf.items() if d
        ) or "  нет данных"

        sweeps_str = "\n".join(
            f"  {'🟢' if s['direction']=='bullish_sweep' else '🔴'} {s['direction']} @ ${s['level']:.4f} (отбой {s['rejection']:.0%})"
            for s in liq.get("sweeps", [])
        ) or "  не обнаружено"

        avwap_levels = avwap.get("levels", {})
        avwap_str = "\n".join(
            f"  {k}: ${v:.4f} ({'▲' if price > v else '▼'})"
            for k, v in avwap_levels.items() if v
        ) or "  нет данных"

        fg   = external.get("fear_greed", {})
        news = external.get("news_headlines", [])
        scores_str = f"TA:{composite_score:+d} LIQ:{liq.get('score',0):+d} OF:{of.get('score',0):+d} AVWAP:{avwap.get('score',0):+d} VP:{vp.get('score',0):+d}"

        prompt = f"""Институциональный трейдер-квант. Day trading сигнал (4-16 часов).

══ {symbol} | ${price:,.4f} | СКОР [{scores_str}] ══

📊 ИНДИКАТОРЫ: RSI:{ind.get('rsi',0):.1f} | MACD:{'▲' if ind.get('macd_hist',0)>0 else '▼'} | EMA:{ind.get('ema_trend','?')} | ADX:{ind.get('adx',0):.1f} | OBV:{ind.get('obv_signal','?')}

📐 MTF:
{mtf_str}

🕯 ПАТТЕРНЫ: {', '.join(patterns) if patterns else 'нет'}

💧 LIQUIDITY (сигнал: {liq.get('signal','?').upper()}):
  Equal Highs: {liq.get('eq_highs',[])} | Equal Lows: {liq.get('eq_lows',[])}
  Buy-side liq: {liq.get('buy_side_liq',[])} | Sell-side liq: {liq.get('sell_side_liq',[])}
  Свипы:
{sweeps_str}
  {liq.get('inducement','') or ''}
  {liq.get('description','')}

📈 ORDER FLOW (сигнал: {of.get('signal','?').upper()}):
  Cum.Delta: {of.get('cum_delta_slope',0):+.0f} | Last delta: {of.get('last_delta',0):+.0f}
  {of.get('divergence','') or '—'}
  {of.get('absorption','') or ''}
  {of.get('effort_result','') or ''}
  {of.get('delta_exhaustion','') or ''}

⚓ ANCHORED VWAP:
{avwap_str}
  {' | '.join(avwap.get('signals',[])[:3])}

📦 VOLUME PROFILE:
  POC:${vp.get('poc',0):.4f} | VAH:${vp.get('vah',0):.4f} | VAL:${vp.get('val',0):.4f}
  {'В Value Area' if vp.get('in_value_area') else ('Выше VAH' if vp.get('above_vah') else 'Ниже VAL')}
  {vp.get('poc_trend','')} | HVN↑:${vp.get('nearest_hvn_above') or '?'} | HVN↓:${vp.get('nearest_hvn_below') or '?'}
  {' | '.join(vp.get('signals',[])[:2])}

🌍 Fear&Greed: {fg.get('value','?')}/100 ({fg.get('label','?')}) | BTC Dom: {external.get('btc_dominance','?')}% | Funding: {external.get('funding_rate','n/a')}
News sentiment: {external.get('news_sentiment','?')} | {correlation or ''}

КЛЮЧЕВЫЕ ВОПРОСЫ:
- Liquidity sweep + order flow подтверждают друг друга?
- Volume Profile даёт зону входа?
- AVWAP уровни как поддержка/сопротивление?

DIRECTION: [LONG/SHORT/HOLD]
CONFIDENCE: [0-100]
TIMEFRAME: [часы]
REASON: [3 предложения]
ENTRY_ZONE: [цены]
STOP_LOSS: [цена + почему]
TAKE_PROFIT_1: [цель + обоснование]
TAKE_PROFIT_2: [цель]
RISK_REWARD: [R:R]
INVALIDATION: [условие отмены сигнала]
RISKS: [2 риска]"""

        try:
            response = self.claude.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=700,
                messages=[{"role": "user", "content": prompt}]
            )
            return response.content[0].text.strip()
        except Exception as e:
            logger.error(f"Claude error: {e}")
            return f"DIRECTION: HOLD\nCONFIDENCE: 0\nTIMEFRAME: n/a\nREASON: Ошибка AI.\nENTRY_ZONE: n/a\nSTOP_LOSS: n/a\nTAKE_PROFIT_1: n/a\nTAKE_PROFIT_2: n/a\nRISK_REWARD: n/a\nINVALIDATION: n/a\nRISKS: n/a"

    def _parse_ai(self, text: str, composite: int) -> tuple:
        direction, confidence = "HOLD", 50
        for line in text.split("\n"):
            if line.startswith("DIRECTION:"):
                d = line.split(":", 1)[1].strip().upper()
                if d in ("LONG", "SHORT", "HOLD"):
                    direction = d
            elif line.startswith("CONFIDENCE:"):
                try:
                    confidence = max(0, min(100, int(line.split(":", 1)[1].strip().replace("%", ""))))
                except Exception:
                    pass
        if direction == "LONG"  and composite < -25: confidence = max(0, confidence - 20)
        if direction == "SHORT" and composite >  25: confidence = max(0, confidence - 20)
        return direction, confidence

    async def weekly_digest(self) -> str:
        symbols = ["BTC-USD", "ETH-USD", "GC=F", "EURUSD=X", "AAPL", "NVDA"]
        results = await asyncio.gather(
            *[self.full_analysis(s) for s in symbols],
            return_exceptions=True
        )
        summaries = []
        for sym, res in zip(symbols, results):
            if isinstance(res, Exception):
                continue
            d, c = res["direction"], res["confidence"]
            emoji = "🟢" if d == "LONG" else ("🔴" if d == "SHORT" else "⚪")
            liq_s = res.get("liquidity", {}).get("signal", "?")
            of_s  = res.get("order_flow", {}).get("signal", "?")
            summaries.append(f"{emoji} <b>{sym}</b>: {d} {c}% | LIQ:{liq_s} OF:{of_s}")

        summary_text = "\n".join(summaries)
        try:
            r = self.claude.messages.create(
                model="claude-sonnet-4-20250514", max_tokens=500,
                messages=[{"role": "user", "content":
                    f"Еженедельный дайджест трейдера. Сигналы:\n{summary_text}\n\n"
                    "Напиши: 1) настроение рынков 2) лучший сигнал 3) главный риск 4) что следить. Кратко, с эмодзи."
                }]
            )
            ai_digest = r.content[0].text.strip()
        except Exception:
            ai_digest = "AI дайджест недоступен"

        return (
            f"📰 <b>РЫНОЧНЫЙ ДАЙДЖЕСТ</b>\n{'─'*30}\n\n"
            f"<b>Сигналы:</b>\n{summary_text}\n\n"
            f"<b>🤖 AI:</b>\n{ai_digest}\n\n⚠️ <i>Не финансовый совет</i>"
        )
