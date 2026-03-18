"""
paper/monitor.py — Авто-монитор рынков 24/7
─────────────────────────────────────────────
Работает в фоне через APScheduler:
  • Каждые 15 минут — быстрый скан (15m таймфрейм)
  • Каждый час — полный анализ всех активов
  • Каждые 5 минут — проверка SL/TP открытых сделок
  • При сигнале confidence > MIN_CONFIDENCE — открывает бумажную сделку
  • При срабатывании SL/TP — закрывает и уведомляет
"""

import asyncio
import logging
from datetime import datetime, timezone

from aiogram import Bot

from core.engine import TradingEngine
from paper.journal import PaperJournal, PaperTrade, TradeStatus
from utils.cache import SignalCache

logger = logging.getLogger(__name__)

# Минимальная уверенность для открытия бумажной сделки
MIN_CONFIDENCE   = 65
# Минимальный составной скор
MIN_COMPOSITE    = 20

# Все активы под мониторингом
WATCHLIST = {
    "crypto":  ["BTC-USD", "ETH-USD", "SOL-USD", "BNB-USD", "XRP-USD", "AVAX-USD"],
    "forex":   ["EURUSD=X", "GBPUSD=X", "USDJPY=X", "AUDUSD=X"],
    "metals":  ["GC=F", "SI=F", "CL=F"],
    "stocks":  ["AAPL", "NVDA", "TSLA", "MSFT", "AMZN"],
}

ALL_SYMBOLS = [s for group in WATCHLIST.values() for s in group]


class MarketMonitor:
    def __init__(self, bot: Bot, engine: TradingEngine,
                 journal: PaperJournal, cache: SignalCache,
                 user_ids: list[int], user_settings: dict):
        self.bot          = bot
        self.engine       = engine
        self.journal      = journal
        self.cache        = cache
        self.user_ids     = user_ids
        self.user_settings= user_settings
        self._scan_lock   = asyncio.Lock()

    # ── Главные задачи для планировщика ───────────────────────────────────────

    async def full_scan(self):
        """Полный анализ всех активов — каждый час"""
        if self._scan_lock.locked():
            logger.info("Scan already running, skipping")
            return

        async with self._scan_lock:
            logger.info(f"🔍 Full scan started: {len(ALL_SYMBOLS)} symbols")
            opened_trades = []
            strong_signals = []

            # Батчами по 4 чтобы не перегружать API
            batch_size = 4
            for i in range(0, len(ALL_SYMBOLS), batch_size):
                batch = ALL_SYMBOLS[i:i+batch_size]
                tasks = [self.engine.full_analysis(sym) for sym in batch]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                for sym, result in zip(batch, results):
                    if isinstance(result, Exception):
                        logger.warning(f"Analysis failed {sym}: {result}")
                        continue

                    self.cache.save(sym, result)

                    # Проверяем качество сигнала
                    conf = result.get("confidence", 0)
                    comp = result.get("composite_score", 0)
                    direction = result.get("direction", "HOLD")

                    # Открываем бумажную сделку если сигнал достаточно сильный
                    if (direction != "HOLD"
                            and conf >= MIN_CONFIDENCE
                            and abs(comp) >= MIN_COMPOSITE):
                        trade = self.journal.open_trade(sym, result)
                        if trade:
                            opened_trades.append(trade)
                            logger.info(f"📝 Auto opened: {sym} {direction} conf:{conf}%")

                    # Сильные сигналы для алерта
                    if conf >= 75 and direction != "HOLD":
                        strong_signals.append(result)

                await asyncio.sleep(2)  # пауза между батчами

            # Уведомления
            if opened_trades:
                await self._notify_opened(opened_trades)
            if strong_signals:
                await self._notify_strong(strong_signals)

            logger.info(f"✅ Full scan done. Opened: {len(opened_trades)} trades")

    async def quick_scan(self):
        """Быстрый скан топ-активов — каждые 15 минут"""
        top_symbols = ["BTC-USD", "ETH-USD", "EURUSD=X", "GC=F", "NVDA"]
        tasks = [self.engine.full_analysis(sym) for sym in top_symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for sym, result in zip(top_symbols, results):
            if isinstance(result, Exception):
                continue
            self.cache.save(sym, result)
            conf = result.get("confidence", 0)
            direction = result.get("direction", "HOLD")
            comp = result.get("composite_score", 0)
            if direction != "HOLD" and conf >= MIN_CONFIDENCE + 5 and abs(comp) >= MIN_COMPOSITE:
                trade = self.journal.open_trade(sym, result)
                if trade:
                    await self._notify_opened([trade])

    async def check_sl_tp(self):
        """Проверка SL/TP открытых сделок — каждые 5 минут"""
        open_trades = self.journal.get_open_trades()
        if not open_trades:
            return

        symbols = list({t.symbol for t in open_trades})
        tasks = [self.engine.full_analysis(sym) for sym in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        price_map = {}
        for sym, result in zip(symbols, results):
            if not isinstance(result, Exception):
                price_map[sym] = result.get("price", 0)

        closed_trades = []
        for sym, price in price_map.items():
            if price > 0:
                closed = self.journal.update_open_trades(sym, price)
                closed_trades.extend(closed)

        if closed_trades:
            await self._notify_closed(closed_trades)

    # ── Уведомления ───────────────────────────────────────────────────────────

    async def _notify_opened(self, trades: list[PaperTrade]):
        for trade in trades:
            emoji = "🟢" if trade.direction == "LONG" else "🔴"
            text = (
                f"📝 <b>PAPER TRADE ОТКРЫТА</b>\n\n"
                f"{emoji} <b>{trade.symbol}</b> {trade.direction}\n"
                f"💲 Вход: <b>${trade.entry_price:,.4f}</b>\n"
                f"🛑 Стоп: ${trade.stop_loss:,.4f}\n"
                f"🎯 TP1: ${trade.take_profit1:,.4f}\n"
                f"🎯 TP2: ${trade.take_profit2:,.4f}\n"
                f"💪 Уверенность: {trade.confidence}%\n"
                f"📊 Составной скор: {trade.composite_score:+d}\n"
                f"💰 Виртуальный размер: ${trade.size_usd:.0f}\n\n"
                f"LIQ:{trade.liq_signal} | OF:{trade.of_signal} | VP:{trade.vp_signal}"
            )
            await self._broadcast(text)

    async def _notify_closed(self, trades: list[PaperTrade]):
        for trade in trades:
            pnl_emoji = "✅" if trade.status == TradeStatus.WIN else ("❌" if trade.status == TradeStatus.LOSS else "➖")
            reason_emoji = {"TP1": "🎯", "TP2": "🎯🎯", "SL": "🛑", "timeout": "⏰", "signal_flip": "🔄", "manual": "👤"}.get(trade.close_reason, "📌")
            text = (
                f"{pnl_emoji} <b>PAPER TRADE ЗАКРЫТА</b>\n\n"
                f"<b>{trade.symbol}</b> {trade.direction} | {reason_emoji} {trade.close_reason.upper()}\n"
                f"Вход: ${trade.entry_price:,.4f} → Выход: ${trade.exit_price:,.4f}\n"
                f"P&L: <b>${trade.pnl_usd:+.2f} ({trade.pnl_pct:+.2f}%)</b>\n"
                f"⏱ Длительность: {trade.duration_min} мин"
            )
            await self._broadcast(text)

    async def _notify_strong(self, signals: list[dict]):
        lines = ["🔥 <b>СИЛЬНЫЕ СИГНАЛЫ</b>\n"]
        for r in signals[:5]:
            emoji = "🟢" if r["direction"] == "LONG" else "🔴"
            lines.append(f"{emoji} {r['symbol']}: {r['direction']} {r['confidence']}% (скор:{r.get('composite_score',0):+d})")
        await self._broadcast("\n".join(lines))

    async def _broadcast(self, text: str):
        for uid in self.user_ids:
            try:
                await self.bot.send_message(uid, text, parse_mode="HTML")
            except Exception as e:
                logger.warning(f"Notify {uid} failed: {e}")

    # ── Статус монитора ───────────────────────────────────────────────────────

    def status_text(self) -> str:
        open_trades = self.journal.get_open_trades()
        lines = [
            f"🤖 <b>Монитор активен</b>",
            f"📡 Отслеживаю {len(ALL_SYMBOLS)} активов",
            f"📝 Открытых бумажных сделок: {len(open_trades)}",
            f"⏱ Следующий полный скан: каждый час",
            f"⚡ Быстрый скан: каждые 15 мин",
            f"🔎 Проверка SL/TP: каждые 5 мин",
        ]
        if open_trades:
            lines.append("\n<b>Открытые сделки:</b>")
            for t in open_trades[:5]:
                emoji = "🟢" if t.direction == "LONG" else "🔴"
                lines.append(f"  {emoji} {t.symbol} {t.direction} @ ${t.entry_price:,.4f}")
        return "\n".join(lines)
