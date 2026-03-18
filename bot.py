"""
🧠 TradingMind Bot v3.0 — Paper Trading + Auto-Monitor
───────────────────────────────────────────────────────
Новое в v3:
  • Авто-мониторинг 24 символов 24/7
  • Paper trading журнал (открытие/закрытие по SL/TP автоматически)
  • Статистика: день/неделя/месяц/3 месяца/всё время
  • Win rate, Profit Factor, Max Drawdown, Sharpe, стрики
  • Уведомления при открытии/закрытии сделки
"""

import asyncio
import logging
import os
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import CommandStart, Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from core.engine import TradingEngine
from paper.journal import PaperJournal
from paper.monitor import MarketMonitor, ALL_SYMBOLS, WATCHLIST
from paper.stats_formatter import format_stats, format_recent_trades, format_open_trades
from utils.cache import SignalCache

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger(__name__)

bot       = Bot(token=os.getenv("BOT_TOKEN"))
dp        = Dispatcher(storage=MemoryStorage())
engine    = TradingEngine(anthropic_key=os.getenv("ANTHROPIC_API_KEY"))
journal   = PaperJournal()
cache     = SignalCache()
scheduler = AsyncIOScheduler()

user_settings: dict[int, dict] = {}
subscribed_users: list[int] = []   # получают авто-уведомления

monitor = MarketMonitor(
    bot=bot, engine=engine, journal=journal, cache=cache,
    user_ids=subscribed_users, user_settings=user_settings
)

MARKETS = {
    "crypto":  {"label": "🪙 Крипта",         "symbols": WATCHLIST["crypto"]},
    "forex":   {"label": "💱 Форекс",          "symbols": WATCHLIST["forex"]},
    "metals":  {"label": "🥇 Металлы/Фьючерсы","symbols": WATCHLIST["metals"]},
    "stocks":  {"label": "📈 Акции",            "symbols": WATCHLIST["stocks"]},
}

# ── Клавиатуры ────────────────────────────────────────────────────────────────

def main_kb():
    b = InlineKeyboardBuilder()
    for k, m in MARKETS.items():
        b.button(text=m["label"], callback_data=f"mkt:{k}")
    b.button(text="⚡ Мега-скан",          callback_data="megascan")
    b.button(text="📊 Paper Trading",       callback_data="paper_menu")
    b.button(text="🤖 Статус монитора",     callback_data="monitor_status")
    b.button(text="📰 Дайджест",            callback_data="digest")
    b.adjust(2, 2, 1, 1, 1)
    return b.as_markup()

def paper_menu_kb():
    b = InlineKeyboardBuilder()
    b.button(text="📈 Статистика",          callback_data="stats_menu")
    b.button(text="📂 Открытые позиции",    callback_data="open_positions")
    b.button(text="📋 Последние сделки",    callback_data="recent_trades")
    b.button(text="🔔 Подписка на алерты",  callback_data="toggle_subscribe")
    b.button(text="🚀 Запустить скан сейчас",callback_data="manual_scan")
    b.button(text="◀️ Назад",              callback_data="back_main")
    b.adjust(2, 2, 1, 1)
    return b.as_markup()

def stats_period_kb():
    b = InlineKeyboardBuilder()
    periods = [
        ("📅 Сегодня",   "stats:today"),
        ("📅 Неделя",    "stats:week"),
        ("📆 Месяц",     "stats:month"),
        ("📊 3 месяца",  "stats:3month"),
        ("🏆 Всё время", "stats:all"),
    ]
    for label, cb in periods:
        b.button(text=label, callback_data=cb)
    b.button(text="◀️ Назад", callback_data="paper_menu")
    b.adjust(2, 2, 1, 1)
    return b.as_markup()

def symbols_kb(mkt_key: str):
    b = InlineKeyboardBuilder()
    for sym in MARKETS[mkt_key]["symbols"]:
        label = sym.replace("-USD","").replace("=X","").replace("=F","")
        b.button(text=label, callback_data=f"analyze:{sym}")
    b.button(text="◀️ Назад", callback_data="back_main")
    b.adjust(3)
    return b.as_markup()

def signal_kb(symbol: str):
    b = InlineKeyboardBuilder()
    b.button(text="📝 Открыть бумажную сделку", callback_data=f"paper_open:{symbol}")
    b.button(text="🔄 Обновить",               callback_data=f"analyze:{symbol}")
    b.button(text="◀️ Меню",                  callback_data="back_main")
    b.adjust(1, 2)
    return b.as_markup()

def back_kb(to: str = "back_main"):
    b = InlineKeyboardBuilder()
    b.button(text="◀️ Назад", callback_data=to)
    return b.as_markup()

# ── Команды ───────────────────────────────────────────────────────────────────

@dp.message(CommandStart())
async def cmd_start(message: Message):
    uid = message.from_user.id
    if uid not in subscribed_users:
        subscribed_users.append(uid)
    await message.answer(
        "🧠 <b>TradingMind Bot v3.0</b>\n\n"
        "<b>Что умею:</b>\n"
        "• Мониторю 24 актива 24/7 автоматически\n"
        "• Открываю бумажные сделки по сигналам\n"
        "• Закрываю по SL/TP автоматически\n"
        "• Показываю реальную статистику точности\n"
        "• Анализ: Liquidity Sweep + Order Flow + AVWAP + Volume Profile\n\n"
        "⚠️ <i>Paper trading — виртуальные деньги. Не финансовый совет.</i>",
        parse_mode="HTML", reply_markup=main_kb()
    )

@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    stats = journal.stats("all")
    await message.answer(format_stats(stats), parse_mode="HTML", reply_markup=stats_period_kb())

@dp.message(Command("scan"))
async def cmd_scan(message: Message):
    await message.answer("🔍 Запускаю полный скан...", parse_mode="HTML")
    await monitor.full_scan()

@dp.message(Command("open"))
async def cmd_open(message: Message):
    trades = journal.get_open_trades()
    await message.answer(format_open_trades(trades), parse_mode="HTML")

# ── Callbacks ─────────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "back_main")
async def cb_back(call: CallbackQuery):
    await call.message.edit_text("📊 Главное меню:", reply_markup=main_kb())

@dp.callback_query(F.data.startswith("mkt:"))
async def cb_market(call: CallbackQuery):
    key = call.data.split(":")[1]
    m = MARKETS[key]
    await call.message.edit_text(
        f"{m['label']} — выбери инструмент:",
        reply_markup=symbols_kb(key)
    )

@dp.callback_query(F.data == "paper_menu")
async def cb_paper_menu(call: CallbackQuery):
    open_count = len(journal.get_open_trades())
    stats_all  = journal.stats("all")
    total      = stats_all.get("total", 0)
    pnl        = stats_all.get("total_pnl", 0.0)
    wr         = stats_all.get("win_rate", 0.0)
    pnl_emoji  = "📈" if pnl > 0 else ("📉" if pnl < 0 else "➖")

    text = (
        f"📊 <b>Paper Trading</b>\n\n"
        f"Открытых позиций: <b>{open_count}</b>\n"
        f"Всего сделок: <b>{total}</b>\n"
        f"Win Rate: <b>{wr:.1f}%</b>\n"
        f"Общий P&L: <b>{pnl_emoji} ${pnl:+.2f}</b>"
    )
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=paper_menu_kb())

@dp.callback_query(F.data == "stats_menu")
async def cb_stats_menu(call: CallbackQuery):
    await call.message.edit_text(
        "📊 <b>Выбери период статистики:</b>",
        parse_mode="HTML", reply_markup=stats_period_kb()
    )

@dp.callback_query(F.data.startswith("stats:"))
async def cb_stats(call: CallbackQuery):
    period = call.data.split(":")[1]
    await call.answer("Считаю...")
    stats = journal.stats(period)
    text  = format_stats(stats)
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=stats_period_kb())

@dp.callback_query(F.data == "open_positions")
async def cb_open_positions(call: CallbackQuery):
    trades = journal.get_open_trades()
    text   = format_open_trades(trades)
    b = InlineKeyboardBuilder()
    b.button(text="🔄 Обновить", callback_data="open_positions")
    b.button(text="◀️ Назад",   callback_data="paper_menu")
    b.adjust(2)
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=b.as_markup())

@dp.callback_query(F.data == "recent_trades")
async def cb_recent(call: CallbackQuery):
    trades = journal.recent_trades(15)
    text   = format_recent_trades(trades, 15)
    b = InlineKeyboardBuilder()
    b.button(text="◀️ Назад", callback_data="paper_menu")
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=b.as_markup())

@dp.callback_query(F.data == "toggle_subscribe")
async def cb_subscribe(call: CallbackQuery):
    uid = call.from_user.id
    if uid in subscribed_users:
        subscribed_users.remove(uid)
        await call.answer("🔕 Уведомления отключены")
    else:
        subscribed_users.append(uid)
        await call.answer("🔔 Уведомления включены")
    await cb_paper_menu(call)

@dp.callback_query(F.data == "manual_scan")
async def cb_manual_scan(call: CallbackQuery):
    await call.message.edit_text(
        "🔍 <b>Запускаю полный скан...</b>\n"
        f"Анализирую {len(ALL_SYMBOLS)} активов\n"
        "⏳ ~2-3 минуты",
        parse_mode="HTML"
    )
    asyncio.create_task(monitor.full_scan())
    await asyncio.sleep(2)
    await call.message.edit_text(
        "✅ Скан запущен в фоне.\nУведомления придут при открытии сделок.",
        reply_markup=paper_menu_kb()
    )

@dp.callback_query(F.data == "monitor_status")
async def cb_monitor_status(call: CallbackQuery):
    text = monitor.status_text()
    b = InlineKeyboardBuilder()
    b.button(text="🔄 Обновить",       callback_data="monitor_status")
    b.button(text="🚀 Запустить скан", callback_data="manual_scan")
    b.button(text="◀️ Назад",         callback_data="back_main")
    b.adjust(2, 1)
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=b.as_markup())

@dp.callback_query(F.data.startswith("analyze:"))
async def cb_analyze(call: CallbackQuery):
    sym = call.data.split(":")[1]
    await _run_analysis(call.message, sym, edit=True)

@dp.callback_query(F.data.startswith("paper_open:"))
async def cb_paper_open(call: CallbackQuery):
    sym    = call.data.split(":")[1]
    cached = cache.get_history(sym)
    if not cached:
        await call.answer("Сначала запроси анализ актива")
        return
    # Получаем последний результат из кэша (реконструируем минимально)
    last = cached[-1]
    result = {
        "direction":       last.get("direction", "HOLD"),
        "confidence":      last.get("confidence", 0),
        "price":           last.get("price", 0),
        "composite_score": last.get("ta_score", 0),
        "ai_analysis":     "",
        "liquidity":       {}, "order_flow": {}, "volume_profile": {},
    }
    trade = journal.open_trade(sym, result)
    if trade:
        emoji = "🟢" if trade.direction == "LONG" else "🔴"
        await call.answer(f"✅ Открыта: {sym} {trade.direction}")
        await call.message.reply(
            f"📝 <b>Бумажная сделка открыта</b>\n\n"
            f"{emoji} <b>{sym}</b> {trade.direction} @ ${trade.entry_price:,.4f}\n"
            f"🛑 SL: ${trade.stop_loss:,.4f}\n"
            f"🎯 TP1: ${trade.take_profit1:,.4f}\n"
            f"🎯 TP2: ${trade.take_profit2:,.4f}",
            parse_mode="HTML"
        )
    else:
        await call.answer("Сигнал HOLD или сделка уже открыта", show_alert=True)

@dp.callback_query(F.data == "megascan")
async def cb_megascan(call: CallbackQuery):
    await call.message.edit_text("⚡ <b>Мега-скан...</b>\n⏳ ~30-60 сек", parse_mode="HTML")
    symbols = ["BTC-USD", "ETH-USD", "SOL-USD", "EURUSD=X", "GC=F", "NVDA", "TSLA"]
    tasks   = [engine.full_analysis(s) for s in symbols]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    lines = ["⚡ <b>МЕГА-СКАН</b>\n"]
    for sym, res in zip(symbols, results):
        if isinstance(res, Exception):
            lines.append(f"⚠️ {sym}: ошибка")
            continue
        d = res["direction"]
        c = res["confidence"]
        comp = res.get("composite_score", 0)
        liq_s = res.get("liquidity", {}).get("signal", "?")[0].upper()
        of_s  = res.get("order_flow", {}).get("signal", "?")[0].upper()
        emoji = "🟢" if d == "LONG" else ("🔴" if d == "SHORT" else "⚪")
        bar   = "█" * (c // 20) + "░" * (5 - c // 20)
        lines.append(f"{emoji} <b>{sym}</b>: {d} {c}% [{bar}] скор:{comp:+d} L:{liq_s} F:{of_s}")
        cache.save(sym, res)

    lines.append("\n<i>L=Liquidity F=Flow</i>")
    await call.message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=main_kb())

@dp.callback_query(F.data == "digest")
async def cb_digest(call: CallbackQuery):
    await call.message.edit_text("📰 <b>Формирую дайджест...</b>", parse_mode="HTML")
    try:
        digest = await engine.weekly_digest()
        await call.message.edit_text(digest, parse_mode="HTML", reply_markup=back_kb())
    except Exception as e:
        await call.message.edit_text(f"❌ Ошибка: {e}", reply_markup=back_kb())

# ── Анализ ────────────────────────────────────────────────────────────────────

async def _run_analysis(msg: Message, symbol: str, edit: bool = False):
    loading = f"🔍 <b>{symbol}</b> — анализирую...\n⏳ ~20-40 сек"
    sent = msg if edit else None
    if edit:
        await msg.edit_text(loading, parse_mode="HTML")
    else:
        sent = await msg.answer(loading, parse_mode="HTML")

    try:
        result = await engine.full_analysis(symbol)
        cache.save(symbol, result)
        text = _format_result(symbol, result)
        target = msg if edit else sent
        await target.edit_text(text, parse_mode="HTML", reply_markup=signal_kb(symbol))
    except Exception as e:
        logger.error(f"Analysis error {symbol}: {e}")
        target = msg if edit else sent
        await target.edit_text(
            f"❌ Ошибка анализа <b>{symbol}</b>\n{str(e)[:200]}",
            parse_mode="HTML", reply_markup=back_kb()
        )

def _format_result(symbol: str, r: dict) -> str:
    d    = r.get("direction", "HOLD")
    c    = r.get("confidence", 0)
    price= r.get("price", 0)
    ind  = r.get("indicators", {})
    mtf  = r.get("multi_timeframe", {})
    liq  = r.get("liquidity", {})
    of   = r.get("order_flow", {})
    avwap= r.get("anchored_vwap", {})
    vp   = r.get("volume_profile", {})
    ext  = r.get("external", {})
    ai   = r.get("ai_analysis", "")
    comp = r.get("composite_score", 0)

    dir_map = {"LONG": "🟢 LONG", "SHORT": "🔴 SHORT", "HOLD": "⚪ HOLD"}
    bar = "█" * (c // 10) + "░" * (10 - c // 10)

    mtf_lines = []
    for tf, sig in mtf.items():
        if sig:
            e = "🟢" if sig.get("bias") == "bullish" else ("🔴" if sig.get("bias") == "bearish" else "⚪")
            mtf_lines.append(f"  {e} {tf}: {sig.get('bias','?').upper()} ADX:{sig.get('adx',0):.0f}")

    liq_e  = "🟢" if liq.get("signal") == "bullish" else ("🔴" if liq.get("signal") == "bearish" else "⚪")
    of_e   = "🟢" if of.get("signal")  == "bullish" else ("🔴" if of.get("signal")  == "bearish" else "⚪")
    vp_e   = "🟢" if vp.get("signal")  == "bullish" else ("🔴" if vp.get("signal")  == "bearish" else "⚪")
    avwap_e= "🟢" if avwap.get("signal")=="bullish" else ("🔴" if avwap.get("signal")=="bearish" else "⚪")

    sweeps = liq.get("sweeps", [])
    sweep_str = ""
    for s in sweeps[:2]:
        se = "🟢" if "bullish" in s["direction"] else "🔴"
        sweep_str += f"\n  {se} {s['direction']} @ ${s['level']:.4f}"

    fg = ext.get("fear_greed", {})
    funding = ext.get("funding_rate", "")

    # Парсим AI для краткого вывода
    ai_lines = [l for l in ai.split("\n") if any(
        l.startswith(k) for k in ["DIRECTION","CONFIDENCE","REASON","ENTRY_ZONE","STOP_LOSS","TAKE_PROFIT_1","RISK_REWARD","INVALIDATION"]
    )]
    ai_short = "\n".join(ai_lines[:8])

    sep = "─" * 30
    dir_label = dir_map.get(d, "⚪ HOLD")
    mtf_str = "\n".join(mtf_lines) or "  нет"
    macd_arrow = "▲" if ind.get("macd_hist", 0) > 0 else "▼"
    ema_trend = ind.get("ema_trend", "?")
    liq_desc = liq.get("description", "")[:80]
    of_summary = of.get("summary", "")[:80]
    avwap_above = avwap.get("above_count", 0)
    avwap_total = len(avwap.get("levels", {}))
    poc = vp.get("poc", 0)
    vp_zone = "В VA" if vp.get("in_value_area") else ("▲ VAH" if vp.get("above_vah") else "▼ VAL")
    fg_val = fg.get("value", "?")
    fg_label = fg.get("label", "?")
    btc_dom = ext.get("btc_dominance", "?")
    news_sent = ext.get("news_sentiment", "?")
    funding_str = f" | Funding:{funding}" if funding else ""

    return (
        f"🧠 <b>{symbol}</b>\n{sep}\n"
        f"🎯 <b>{dir_label}</b>  Уверенность: <b>{c}%</b>\n"
        f"[{bar}]  Скор: <b>{comp:+d}/100</b>\n"
        f"💲 <b>${price:,.4f}</b>\n\n"

        f"📐 <b>MTF:</b>\n{mtf_str}\n\n"

        f"📊 <b>Индикаторы:</b> RSI:{ind.get('rsi', 0):.1f} | MACD:{macd_arrow} | EMA:{ema_trend} | ADX:{ind.get('adx', 0):.1f}\n\n"

        f"💧 {liq_e} <b>Liquidity</b> ({liq.get('score', 0):+d}): {liq_desc}"
        + (sweep_str + "\n\n" if sweeps else "\n\n") +

        f"📈 {of_e} <b>Order Flow</b> ({of.get('score', 0):+d}): {of_summary}\n\n"

        f"⚓ {avwap_e} <b>AVWAP</b>: цена выше {avwap_above}/{avwap_total} уровней\n"
        f"📦 {vp_e} <b>Vol Profile</b>: POC ${poc:.4f} | {vp_zone}\n\n"

        f"🌍 F&G:{fg_val}/100 ({fg_label}) | Dom:{btc_dom}% | {news_sent}{funding_str}\n\n"

        f"🤖 <b>AI Вердикт:</b>\n<code>{ai_short}</code>\n\n"
        f"⚠️ <i>Не финансовый совет</i>"
    )

# ── Запуск ────────────────────────────────────────────────────────────────────

async def main():
    # Планировщик
    scheduler.add_job(monitor.full_scan,    "interval", hours=1,    id="full_scan")
    scheduler.add_job(monitor.quick_scan,   "interval", minutes=15, id="quick_scan")
    scheduler.add_job(monitor.check_sl_tp,  "interval", minutes=5,  id="sl_tp_check")
    scheduler.add_job(
        lambda: asyncio.create_task(
            _send_weekly_digest()
        ),
        "cron", day_of_week="mon", hour=9, id="weekly"
    )
    scheduler.start()
    logger.info(f"✅ Scheduler started | Monitoring {len(ALL_SYMBOLS)} symbols")

    # Первый скан при старте (в фоне)
    asyncio.create_task(monitor.full_scan())

    await dp.start_polling(bot)


async def _send_weekly_digest():
    try:
        digest = await engine.weekly_digest()
        for uid in subscribed_users:
            await bot.send_message(uid, digest, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Weekly digest error: {e}")


if __name__ == "__main__":
    asyncio.run(main())