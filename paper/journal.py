"""
paper/journal.py — Paper Trading Journal
─────────────────────────────────────────
Полноценный виртуальный трейдинг-журнал:
  • Открытие/закрытие бумажных сделок
  • Расчёт P&L с учётом комиссий и проскальзывания
  • Статистика за день/неделю/месяц/всё время
  • Win Rate, Profit Factor, Max Drawdown, Sharpe Ratio
  • История всех сигналов и их исходы
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
from enum import Enum

logger = logging.getLogger(__name__)

JOURNAL_FILE = Path("/opt/TradingBot/data/paper_journal.json")

# Реалистичные комиссии
COMMISSION_RATE = 0.0004   # 0.04% — Binance фьючерсы maker
SLIPPAGE_RATE   = 0.0002   # 0.02% — проскальзывание
LEVERAGE        = 3         # виртуальное плечо


class TradeStatus(str, Enum):
    OPEN   = "open"
    WIN    = "win"
    LOSS   = "loss"
    BREAK  = "breakeven"


@dataclass
class PaperTrade:
    id:           str
    symbol:       str
    direction:    str          # LONG | SHORT
    entry_price:  float
    stop_loss:    float
    take_profit1: float
    take_profit2: float
    size_usd:     float        # виртуальный размер позиции
    confidence:   int
    composite_score: int
    opened_at:    str          # ISO datetime
    closed_at:    Optional[str] = None
    exit_price:   Optional[float] = None
    status:       TradeStatus = TradeStatus.OPEN
    pnl_usd:      float = 0.0
    pnl_pct:      float = 0.0
    close_reason: str = ""     # "TP1" | "TP2" | "SL" | "manual" | "timeout" | "signal_flip"
    ai_analysis:  str = ""
    liq_signal:   str = ""
    of_signal:    str = ""
    vp_signal:    str = ""
    duration_min: int = 0


class PaperJournal:
    """Главный журнал бумажных сделок"""

    POSITION_SIZE_USD = 100.0    # виртуальный размер каждой позиции

    def __init__(self):
        self.trades: list[PaperTrade] = []
        self._load()

    # ── CRUD ─────────────────────────────────────────────────────────────────

    def open_trade(self, symbol: str, result: dict) -> Optional[PaperTrade]:
        """Открывает новую бумажную сделку на основе сигнала"""
        direction = result.get("direction", "HOLD")
        if direction == "HOLD":
            return None

        # Проверяем нет ли уже открытой сделки по этому символу
        existing = self.get_open_trade(symbol)
        if existing:
            # Если сигнал противоположный — закрываем старую
            if existing.direction != direction:
                self.close_trade(existing.id, result.get("price", existing.entry_price),
                                 reason="signal_flip")
            else:
                return None  # уже открыта в том же направлении

        price = result.get("price", 0)
        if price <= 0:
            return None

        ai_text = result.get("ai_analysis", "")
        entry, stop, tp1, tp2 = self._parse_levels(ai_text, price, direction)

        trade = PaperTrade(
            id=f"{symbol}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
            symbol=symbol,
            direction=direction,
            entry_price=price,
            stop_loss=stop,
            take_profit1=tp1,
            take_profit2=tp2,
            size_usd=self.POSITION_SIZE_USD * LEVERAGE,
            confidence=result.get("confidence", 0),
            composite_score=result.get("composite_score", 0),
            opened_at=datetime.now(timezone.utc).isoformat(),
            ai_analysis=ai_text[:300],
            liq_signal=result.get("liquidity", {}).get("signal", ""),
            of_signal=result.get("order_flow", {}).get("signal", ""),
            vp_signal=result.get("volume_profile", {}).get("signal", ""),
        )

        self.trades.append(trade)
        self._save()
        logger.info(f"📝 Paper trade opened: {symbol} {direction} @ ${price:.4f}")
        return trade

    def update_open_trades(self, symbol: str, current_price: float) -> list[PaperTrade]:
        """Проверяет открытые сделки — достигнут ли SL/TP"""
        closed = []
        for trade in self.trades:
            if trade.symbol != symbol or trade.status != TradeStatus.OPEN:
                continue

            if trade.direction == "LONG":
                if current_price <= trade.stop_loss:
                    self.close_trade(trade.id, current_price, reason="SL")
                    closed.append(trade)
                elif trade.take_profit2 > 0 and current_price >= trade.take_profit2:
                    self.close_trade(trade.id, current_price, reason="TP2")
                    closed.append(trade)
                elif trade.take_profit1 > 0 and current_price >= trade.take_profit1:
                    self.close_trade(trade.id, current_price, reason="TP1")
                    closed.append(trade)

            elif trade.direction == "SHORT":
                if current_price >= trade.stop_loss:
                    self.close_trade(trade.id, current_price, reason="SL")
                    closed.append(trade)
                elif trade.take_profit2 > 0 and current_price <= trade.take_profit2:
                    self.close_trade(trade.id, current_price, reason="TP2")
                    closed.append(trade)
                elif trade.take_profit1 > 0 and current_price <= trade.take_profit1:
                    self.close_trade(trade.id, current_price, reason="TP1")
                    closed.append(trade)

            # Таймаут: сделка открыта > 24 часов — закрываем по текущей цене
            opened = datetime.fromisoformat(trade.opened_at)
            age_hours = (datetime.now(timezone.utc) - opened).total_seconds() / 3600
            if age_hours > 24 and trade.status == TradeStatus.OPEN:
                self.close_trade(trade.id, current_price, reason="timeout")
                closed.append(trade)

        return closed

    def close_trade(self, trade_id: str, exit_price: float, reason: str = "manual") -> Optional[PaperTrade]:
        trade = self._find(trade_id)
        if not trade or trade.status != TradeStatus.OPEN:
            return None

        # P&L с учётом комиссии и проскальзывания
        total_fee = (COMMISSION_RATE + SLIPPAGE_RATE) * 2  # вход + выход
        if trade.direction == "LONG":
            raw_pnl_pct = (exit_price - trade.entry_price) / trade.entry_price
        else:
            raw_pnl_pct = (trade.entry_price - exit_price) / trade.entry_price

        net_pnl_pct = raw_pnl_pct - total_fee
        pnl_usd     = trade.size_usd * net_pnl_pct

        # Длительность
        opened = datetime.fromisoformat(trade.opened_at)
        duration_min = int((datetime.now(timezone.utc) - opened).total_seconds() / 60)

        trade.exit_price   = exit_price
        trade.closed_at    = datetime.now(timezone.utc).isoformat()
        trade.close_reason = reason
        trade.pnl_usd      = round(pnl_usd, 4)
        trade.pnl_pct      = round(net_pnl_pct * 100, 3)
        trade.duration_min = duration_min

        if pnl_usd > 0.5:    trade.status = TradeStatus.WIN
        elif pnl_usd < -0.5: trade.status = TradeStatus.LOSS
        else:                 trade.status = TradeStatus.BREAK

        self._save()
        logger.info(f"📊 Paper trade closed: {trade.id} {trade.status} P&L: ${pnl_usd:+.2f}")
        return trade

    def get_open_trade(self, symbol: str) -> Optional[PaperTrade]:
        for t in self.trades:
            if t.symbol == symbol and t.status == TradeStatus.OPEN:
                return t
        return None

    def get_open_trades(self) -> list[PaperTrade]:
        return [t for t in self.trades if t.status == TradeStatus.OPEN]

    # ── Статистика ────────────────────────────────────────────────────────────

    def stats(self, period: str = "all") -> dict:
        """
        period: "today" | "week" | "month" | "3month" | "all"
        """
        closed = self._filter_by_period(period)
        closed = [t for t in closed if t.status != TradeStatus.OPEN]

        if not closed:
            return {"period": period, "total": 0, "empty": True}

        wins   = [t for t in closed if t.status == TradeStatus.WIN]
        losses = [t for t in closed if t.status == TradeStatus.LOSS]
        breaks = [t for t in closed if t.status == TradeStatus.BREAK]

        total_pnl   = sum(t.pnl_usd for t in closed)
        gross_win   = sum(t.pnl_usd for t in wins)
        gross_loss  = abs(sum(t.pnl_usd for t in losses))
        win_rate    = len(wins) / len(closed) * 100 if closed else 0
        profit_factor = gross_win / gross_loss if gross_loss > 0 else float("inf")

        # Max Drawdown
        running_pnl = 0
        peak        = 0
        max_dd      = 0
        for t in sorted(closed, key=lambda x: x.closed_at or ""):
            running_pnl += t.pnl_usd
            if running_pnl > peak:
                peak = running_pnl
            dd = peak - running_pnl
            if dd > max_dd:
                max_dd = dd

        # Streak
        sorted_trades = sorted(closed, key=lambda x: x.closed_at or "")
        win_streak = loss_streak = cur_win = cur_loss = 0
        for t in sorted_trades:
            if t.status == TradeStatus.WIN:
                cur_win  += 1; cur_loss = 0
            elif t.status == TradeStatus.LOSS:
                cur_loss += 1; cur_win  = 0
            win_streak  = max(win_streak,  cur_win)
            loss_streak = max(loss_streak, cur_loss)

        avg_win  = gross_win  / len(wins)   if wins   else 0
        avg_loss = gross_loss / len(losses) if losses else 0
        avg_duration = sum(t.duration_min for t in closed) / len(closed) if closed else 0

        # Best / Worst
        best  = max(closed, key=lambda t: t.pnl_usd)
        worst = min(closed, key=lambda t: t.pnl_usd)

        # По символам
        by_symbol = {}
        for t in closed:
            if t.symbol not in by_symbol:
                by_symbol[t.symbol] = {"wins": 0, "losses": 0, "pnl": 0}
            by_symbol[t.symbol]["pnl"] += t.pnl_usd
            if t.status == TradeStatus.WIN:   by_symbol[t.symbol]["wins"]   += 1
            elif t.status == TradeStatus.LOSS: by_symbol[t.symbol]["losses"] += 1

        # По направлению
        longs  = [t for t in closed if t.direction == "LONG"]
        shorts = [t for t in closed if t.direction == "SHORT"]

        # Причины закрытия
        close_reasons = {}
        for t in closed:
            close_reasons[t.close_reason] = close_reasons.get(t.close_reason, 0) + 1

        return {
            "period":         period,
            "total":          len(closed),
            "wins":           len(wins),
            "losses":         len(losses),
            "breaks":         len(breaks),
            "win_rate":       round(win_rate, 1),
            "total_pnl":      round(total_pnl, 2),
            "gross_win":      round(gross_win, 2),
            "gross_loss":     round(gross_loss, 2),
            "profit_factor":  round(profit_factor, 2),
            "max_drawdown":   round(max_dd, 2),
            "avg_win":        round(avg_win, 2),
            "avg_loss":       round(avg_loss, 2),
            "avg_duration_min": round(avg_duration),
            "win_streak":     win_streak,
            "loss_streak":    loss_streak,
            "best_trade":     {"symbol": best.symbol, "pnl": best.pnl_usd, "dir": best.direction},
            "worst_trade":    {"symbol": worst.symbol, "pnl": worst.pnl_usd, "dir": worst.direction},
            "by_symbol":      by_symbol,
            "longs_count":    len(longs),
            "shorts_count":   len(shorts),
            "longs_pnl":      round(sum(t.pnl_usd for t in longs), 2),
            "shorts_pnl":     round(sum(t.pnl_usd for t in shorts), 2),
            "close_reasons":  close_reasons,
            "open_trades":    len(self.get_open_trades()),
        }

    def recent_trades(self, n: int = 10) -> list[PaperTrade]:
        closed = [t for t in self.trades if t.status != TradeStatus.OPEN]
        return sorted(closed, key=lambda t: t.closed_at or "", reverse=True)[:n]

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _parse_levels(self, ai_text: str, price: float, direction: str) -> tuple:
        """Парсим ENTRY/STOP/TP из AI ответа"""
        entry = stop = tp1 = tp2 = 0.0

        for line in ai_text.split("\n"):
            try:
                if line.startswith("ENTRY_ZONE:"):
                    raw = line.split(":", 1)[1].strip()
                    nums = [float(x.replace("$","").replace(",","").strip())
                            for x in raw.replace("–","-").replace("—","-").split("-") if x.strip()]
                    entry = sum(nums) / len(nums) if nums else price
                elif line.startswith("STOP_LOSS:"):
                    raw = line.split(":", 1)[1].strip()
                    nums = [float(x.replace("$","").replace(",","")) for x in raw.split() if self._is_price(x)]
                    stop = nums[0] if nums else 0
                elif line.startswith("TAKE_PROFIT_1:"):
                    raw = line.split(":", 1)[1].strip()
                    nums = [float(x.replace("$","").replace(",","")) for x in raw.split() if self._is_price(x)]
                    tp1 = nums[0] if nums else 0
                elif line.startswith("TAKE_PROFIT_2:"):
                    raw = line.split(":", 1)[1].strip()
                    nums = [float(x.replace("$","").replace(",","")) for x in raw.split() if self._is_price(x)]
                    tp2 = nums[0] if nums else 0
            except Exception:
                pass

        # Fallback: если AI не дал уровни — считаем по ATR-логике
        if stop == 0:
            stop = price * (0.985 if direction == "LONG" else 1.015)
        if tp1 == 0:
            diff = abs(price - stop)
            tp1 = price + diff * 2 if direction == "LONG" else price - diff * 2
        if tp2 == 0:
            diff = abs(price - stop)
            tp2 = price + diff * 3 if direction == "LONG" else price - diff * 3

        return entry or price, stop, tp1, tp2

    def _is_price(self, s: str) -> bool:
        try:
            float(s.replace("$","").replace(",",""))
            return True
        except Exception:
            return False

    def _filter_by_period(self, period: str) -> list[PaperTrade]:
        now = datetime.now(timezone.utc)
        cutoffs = {
            "today":  now - timedelta(days=1),
            "week":   now - timedelta(weeks=1),
            "month":  now - timedelta(days=30),
            "3month": now - timedelta(days=90),
            "all":    datetime(2000, 1, 1, tzinfo=timezone.utc),
        }
        cutoff = cutoffs.get(period, cutoffs["all"])
        result = []
        for t in self.trades:
            ts_str = t.closed_at or t.opened_at
            try:
                ts = datetime.fromisoformat(ts_str)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts >= cutoff:
                    result.append(t)
            except Exception:
                pass
        return result

    def _find(self, trade_id: str) -> Optional[PaperTrade]:
        for t in self.trades:
            if t.id == trade_id:
                return t
        return None

    def _load(self):
        try:
            JOURNAL_FILE.parent.mkdir(parents=True, exist_ok=True)
            if JOURNAL_FILE.exists():
                with open(JOURNAL_FILE) as f:
                    data = json.load(f)
                self.trades = [PaperTrade(**t) for t in data]
                logger.info(f"Loaded {len(self.trades)} trades from journal")
        except Exception as e:
            logger.warning(f"Journal load failed: {e}")
            self.trades = []

    def _save(self):
        try:
            with open(JOURNAL_FILE, "w") as f:
                json.dump([asdict(t) for t in self.trades], f, indent=2)
        except Exception as e:
            logger.warning(f"Journal save failed: {e}")
