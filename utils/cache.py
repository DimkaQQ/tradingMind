"""
utils/cache.py — Кэш сигналов (в памяти + JSON файл)
utils/backtest.py — Бэктест исторических сигналов
"""

import json
import os
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

CACHE_FILE = Path("/opt/TradingSignalBot/data/signals_history.json")


# ─────────────────────────────────────────────────────────────────────────────
# SignalCache
# ─────────────────────────────────────────────────────────────────────────────

class SignalCache:
    """Сохраняет историю сигналов для бэктеста и аналитики"""

    def __init__(self):
        self._memory: dict[str, list] = {}
        self._load()

    def save(self, symbol: str, result: dict):
        entry = {
            "ts":         datetime.now(timezone.utc).isoformat(),
            "price":      result.get("price", 0),
            "direction":  result.get("direction", "HOLD"),
            "confidence": result.get("confidence", 0),
            "ta_score":   result.get("ta_score", 0),
        }
        if symbol not in self._memory:
            self._memory[symbol] = []
        self._memory[symbol].append(entry)
        # Храним последние 200 сигналов на актив
        self._memory[symbol] = self._memory[symbol][-200:]
        self._persist()

    def get_history(self, symbol: str) -> list:
        return self._memory.get(symbol, [])

    def all_symbols(self) -> list[str]:
        return list(self._memory.keys())

    def _load(self):
        try:
            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            if CACHE_FILE.exists():
                with open(CACHE_FILE) as f:
                    self._memory = json.load(f)
        except Exception as e:
            logger.warning(f"Cache load failed: {e}")
            self._memory = {}

    def _persist(self):
        try:
            with open(CACHE_FILE, "w") as f:
                json.dump(self._memory, f)
        except Exception as e:
            logger.warning(f"Cache persist failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# BacktestEngine
# ─────────────────────────────────────────────────────────────────────────────

class BacktestEngine:
    """
    Псевдо-бэктест: сравниваем исторические сигналы с реальным движением цены.
    Логика: если сигнал был LONG и цена через N свечей выросла — попадание.
    Используется для отображения статистики точности.
    """

    def __init__(self, cache: SignalCache):
        self.cache = cache

    def generate_report(self) -> str:
        symbols = self.cache.all_symbols()
        if not symbols:
            return (
                "📊 <b>Бэктест сигналов</b>\n\n"
                "История пуста. Запроси несколько сигналов — "
                "бот начнёт отслеживать точность."
            )

        lines = ["📊 <b>Бэктест сигналов</b>\n<i>Сравнение с реальным движением цены</i>\n"]
        total_calls = total_correct = 0

        for sym in symbols[:8]:
            history = self.cache.get_history(sym)
            if len(history) < 2:
                continue
            correct, total = self._eval_symbol(history)
            if total == 0:
                continue
            accuracy = correct / total * 100
            total_calls += total
            total_correct += correct
            bar = "█" * int(accuracy // 10) + "░" * (10 - int(accuracy // 10))
            emoji = "🟢" if accuracy >= 60 else ("🟡" if accuracy >= 50 else "🔴")
            lines.append(f"{emoji} <b>{sym}</b>: {accuracy:.0f}% [{bar}] ({total} сигн.)")

        if total_calls > 0:
            overall = total_correct / total_calls * 100
            lines.append(f"\n<b>Итого: {overall:.0f}% точность по {total_calls} сигналам</b>")
            lines.append(
                "\n<i>⚠️ Реальная точность зависит от таймфрейма выхода и стоп-лоссов</i>"
            )
        else:
            lines.append("\nНедостаточно данных для расчёта точности.")

        return "\n".join(lines)

    def symbol_report(self, symbol: str) -> str:
        history = self.cache.get_history(symbol)
        if len(history) < 2:
            return f"📊 <b>Бэктест {symbol}</b>\n\nНедостаточно истории. Запрашивай сигналы регулярно."

        correct, total = self._eval_symbol(history)
        accuracy = correct / total * 100 if total > 0 else 0

        # Статистика по типам
        long_signals  = [h for h in history if h["direction"] == "LONG"]
        short_signals = [h for h in history if h["direction"] == "SHORT"]
        avg_conf = sum(h["confidence"] for h in history) / len(history)

        # Последние 5 сигналов
        recent_lines = []
        for h in history[-5:][::-1]:
            ts = h["ts"][:16].replace("T", " ")
            d = h["direction"]
            c = h["confidence"]
            e = "🟢" if d == "LONG" else ("🔴" if d == "SHORT" else "⚪")
            recent_lines.append(f"  {e} {ts} | {d} {c}%")

        return (
            f"📊 <b>Бэктест {symbol}</b>\n"
            f"{'─' * 28}\n"
            f"Точность: <b>{accuracy:.0f}%</b> ({total} сигналов)\n"
            f"Средняя уверенность: {avg_conf:.0f}%\n"
            f"LONG сигналов: {len(long_signals)}\n"
            f"SHORT сигналов: {len(short_signals)}\n\n"
            f"<b>Последние сигналы:</b>\n"
            + "\n".join(recent_lines) +
            "\n\n<i>⚠️ Псевдо-бэктест по ценовому движению. Для реального бэктеста нужен брокерский API.</i>"
        )

    def _eval_symbol(self, history: list) -> tuple[int, int]:
        """Оцениваем точность: сравниваем сигнал N с ценой в N+1"""
        correct = total = 0
        for i in range(len(history) - 1):
            curr = history[i]
            nxt  = history[i + 1]
            if curr["direction"] == "HOLD":
                continue
            price_change = (nxt["price"] - curr["price"]) / (curr["price"] + 1e-10)
            if curr["direction"] == "LONG" and price_change > 0.003:
                correct += 1
            elif curr["direction"] == "SHORT" and price_change < -0.003:
                correct += 1
            total += 1
        return correct, total
