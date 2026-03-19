"""
paper/stats_formatter.py — Форматирование статистики для Telegram
"""

from paper.journal import PaperJournal, PaperTrade, TradeStatus


def format_stats(stats: dict) -> str:
    if stats.get("empty"):
        period_labels = {
            "today": "сегодня", "week": "за неделю",
            "month": "за месяц", "3month": "за 3 месяца", "all": "за всё время"
        }
        p = period_labels.get(stats["period"], stats["period"])
        return (
            f"📊 <b>Статистика {p}</b>\n\n"
            f"Сделок пока нет. Монитор работает и ищет сигналы.\n\n"
            f"<i>Минимальный порог: confidence ≥ 65% и |score| ≥ 20</i>"
        )

    period_labels = {
        "today": "🗓 Сегодня", "week": "📅 Неделя",
        "month": "📆 Месяц", "3month": "📊 3 месяца", "all": "🏆 Всё время"
    }
    period_str = period_labels.get(stats["period"], stats["period"])

    total  = stats["total"]
    wins   = stats["wins"]
    losses = stats["losses"]
    breaks = stats["breaks"]
    wr     = stats["win_rate"]
    pnl    = stats["total_pnl"]
    pf     = stats["profit_factor"]
    dd     = stats["max_drawdown"]

    # Полоса прогресса win rate
    filled = int(wr / 10)
    wr_bar = "█" * filled + "░" * (10 - filled)

    # P&L цвет
    pnl_emoji = "📈" if pnl > 0 else ("📉" if pnl < 0 else "➖")

    # Profit Factor оценка
    if pf == float("inf"):   pf_str = "∞ (нет убытков!)"
    elif pf >= 2.0:          pf_str = f"{pf:.2f} 🔥"
    elif pf >= 1.5:          pf_str = f"{pf:.2f} ✅"
    elif pf >= 1.0:          pf_str = f"{pf:.2f} ⚠️"
    else:                    pf_str = f"{pf:.2f} ❌"

    # Лучший/худший
    best  = stats.get("best_trade",  {})
    worst = stats.get("worst_trade", {})

    # По символам топ-3
    by_sym = stats.get("by_symbol", {})
    top_symbols = sorted(by_sym.items(), key=lambda x: x[1]["pnl"], reverse=True)

    sym_lines = []
    for sym, data in top_symbols[:5]:
        sym_pnl = data["pnl"]
        sym_wr  = data["wins"] / (data["wins"] + data["losses"]) * 100 if (data["wins"] + data["losses"]) > 0 else 0
        e = "🟢" if sym_pnl > 0 else "🔴"
        sym_lines.append(f"  {e} {sym}: ${sym_pnl:+.2f} (WR:{sym_wr:.0f}%)")

    # Причины закрытия
    reasons = stats.get("close_reasons", {})
    reason_str = " | ".join(f"{k.upper()}:{v}" for k, v in reasons.items())

    # Лонги vs Шорты
    longs_pnl  = stats.get("longs_pnl", 0)
    shorts_pnl = stats.get("shorts_pnl", 0)
    longs_cnt  = stats.get("longs_count", 0)
    shorts_cnt = stats.get("shorts_count", 0)

    text = (
        f"📊 <b>PAPER TRADING — {period_str}</b>\n"
        f"{'─' * 32}\n\n"

        f"📈 <b>Общий P&L: {pnl_emoji} ${pnl:+.2f}</b>\n"
        f"{'─' * 20}\n"
        f"Сделок: <b>{total}</b>  |  ✅{wins}  ❌{losses}  ➖{breaks}\n"
        f"Win Rate: <b>{wr:.1f}%</b>  [{wr_bar}]\n"
        f"Profit Factor: <b>{pf_str}</b>\n"
        f"Max Drawdown: <b>${dd:.2f}</b>\n\n"

        f"💰 <b>Детали P&L:</b>\n"
        f"Средний выигрыш: +${stats['avg_win']:.2f}\n"
        f"Средний убыток:  -${stats['avg_loss']:.2f}\n"
        f"Avg R:R: {stats['avg_win']/stats['avg_loss']:.2f}:1\n" if stats['avg_loss'] > 0 else ""
        f"Avg длительность: {stats['avg_duration_min']} мин\n\n"

        f"🏆 <b>Рекорды:</b>\n"
        f"Лучшая: {best.get('symbol','')} {best.get('dir','')} +${best.get('pnl',0):.2f}\n"
        f"Худшая: {worst.get('symbol','')} {worst.get('dir','')} ${worst.get('pnl',0):.2f}\n"
        f"Макс серия побед: {stats['win_streak']} | Макс серия потерь: {stats['loss_streak']}\n\n"

        f"📊 <b>Лонги vs Шорты:</b>\n"
        f"LONG ({longs_cnt}): ${longs_pnl:+.2f}\n"
        f"SHORT ({shorts_cnt}): ${shorts_pnl:+.2f}\n\n"

        f"🔝 <b>Топ активы:</b>\n"
        + ("\n".join(sym_lines) if sym_lines else "  нет данных") + "\n\n"

        f"📌 <b>Закрытия:</b> {reason_str}\n"
        f"📂 Открытых сейчас: {stats.get('open_trades', 0)}\n\n"
        f"<i>⚠️ Виртуальные деньги. Не реальный трейдинг.</i>"
    )
    return text


def format_recent_trades(trades: list[PaperTrade], n: int = 10) -> str:
    if not trades:
        return "📋 <b>Последние сделки</b>\n\nПока нет закрытых сделок."

    lines = [f"📋 <b>Последние {min(n, len(trades))} сделок</b>\n"]
    for t in trades[:n]:
        status_emoji = {"win": "✅", "loss": "❌", "breakeven": "➖", "open": "🔵"}.get(t.status, "❓")
        dir_emoji    = "🟢" if t.direction == "LONG" else "🔴"
        reason = t.close_reason.upper() if t.close_reason else "OPEN"
        date = t.closed_at[:10] if t.closed_at else t.opened_at[:10]
        lines.append(
            f"{status_emoji} {dir_emoji} <b>{t.symbol}</b> {t.direction} | "
            f"${t.pnl_usd:+.2f} ({t.pnl_pct:+.2f}%) | "
            f"{reason} | {date}"
        )
    return "\n".join(lines)


def format_open_trades(trades: list[PaperTrade]) -> str:
    if not trades:
        return "📂 <b>Открытые позиции</b>\n\nНет открытых позиций."

    lines = [f"📂 <b>Открытые позиции ({len(trades)})</b>\n"]
    for t in trades:
        emoji = "🟢" if t.direction == "LONG" else "🔴"
        lines.append(
            f"{emoji} <b>{t.symbol}</b> {t.direction}\n"
            f"   Вход: ${t.entry_price:,.4f} | SL: ${t.stop_loss:,.4f} | TP1: ${t.take_profit1:,.4f}\n"
            f"   Уверенность: {t.confidence}% | Скор: {t.composite_score:+d}"
        )
    return "\n\n".join(lines)
