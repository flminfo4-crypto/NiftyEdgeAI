"""
Period-by-period backtest breakdown — "1 year weekly = 52 rows, 1 year
monthly = 12 rows, 2 years weekly = 104 rows".

Method note that matters for correctness: this runs ONE continuous backtest
over the whole range and then buckets the resulting trades into periods,
rather than running each week as an isolated backtest. Isolated per-week
runs would cut every position at the Friday boundary, inventing exits that
never happened and destroying any trade held into the following week — the
numbers would look precise but describe a strategy nobody traded. Bucketing
a continuous run reports what the account actually did in each week.

Each period's return is measured against the equity at the START of that
period, so it answers "what did this week earn me" for a compounding
account. Periods where the strategy took no trades are still listed, at
0.00% — a flat week is a real week, and hiding it would inflate the share
of weeks that met the goal.
"""

from datetime import date, datetime, timedelta

from niftyedge_ai_engine import STRATEGY_REGISTRY, run_backtest

from app.services.backtest_service import _INSTRUMENT_MAP, _fetch_candles

_VALID_PERIODS = ("weekly", "monthly")


def _period_key(d: date, period: str):
    if period == "weekly":
        iso = d.isocalendar()
        return (iso[0], iso[1])
    return (d.year, d.month)


def _period_bounds(key, period: str) -> tuple[date, date]:
    if period == "weekly":
        start = date.fromisocalendar(key[0], key[1], 1)
        return start, start + timedelta(days=6)
    year, month = key
    start = date(year, month, 1)
    end = date(year + (month == 12), (month % 12) + 1, 1) - timedelta(days=1)
    return start, end


def _period_label(key, period: str) -> str:
    if period == "weekly":
        start, _ = _period_bounds(key, period)
        return f"{key[0]}-W{key[1]:02d} ({start.strftime('%d %b')})"
    return date(key[0], key[1], 1).strftime("%b %Y")


def _walk_periods(first: date, last: date, period: str):
    """Every consecutive period key between two dates, including empty ones."""
    keys = []
    if period == "weekly":
        cursor = date.fromisocalendar(*first.isocalendar()[:2], 1)
        end = date.fromisocalendar(*last.isocalendar()[:2], 1)
        while cursor <= end:
            keys.append(_period_key(cursor, period))
            cursor += timedelta(weeks=1)
    else:
        y, m = first.year, first.month
        while (y, m) <= (last.year, last.month):
            keys.append((y, m))
            y, m = (y + (m == 12), (m % 12) + 1)
    return keys


def run_period_report(
    strategy: str,
    instrument: str = "NIFTY50_OPTIONS",
    years: float = 1.0,
    period: str = "weekly",
    starting_capital: float = 100_000.0,
    position_size_lots: int = 1,
    stop_loss_pct: float = 1.5,
    target_pct: float = 3.0,
    target_return_pct: float = 1.0,
    frm_date: str | None = None,
    to_date: str | None = None,
    hold_mode: str = "strategy",
    custom_hold_days: int = 5,
) -> dict:
    if strategy not in STRATEGY_REGISTRY:
        raise ValueError(f"Unknown strategy '{strategy}'")
    if period not in _VALID_PERIODS:
        raise ValueError(f"period must be one of {_VALID_PERIODS}")

    underlying, is_futures = _INSTRUMENT_MAP.get(instrument, ("NIFTY50", False))
    # Explicit dates win when given (the Backtester form passes its own
    # range); otherwise fall back to the trailing `years` window.
    to = date.fromisoformat(to_date) if to_date else date.today()
    frm = date.fromisoformat(frm_date) if frm_date else to - timedelta(days=int(365 * years))
    if frm >= to:
        raise ValueError("'from' must be before 'to'")
    candles = _fetch_candles(underlying, frm, to)
    if not candles:
        raise ValueError(f"No historical candles for {underlying}")

    in_range = [c for c in candles if c.dt >= frm]
    if len(in_range) < 20:
        raise ValueError("Not enough trading days in the requested range")

    result = run_backtest(
        candles=candles, strategy=strategy, starting_capital=starting_capital,
        position_size_lots=position_size_lots, stop_loss_pct=stop_loss_pct,
        target_pct=target_pct, include_slippage_and_costs=True, is_futures=is_futures,
        hold_mode=hold_mode, custom_hold_days=custom_hold_days,
    )
    # Only trades that closed inside the requested window count toward it;
    # the extra leading candles exist purely to warm up CPR/volatility.
    trades = [t for t in result.trades if t.closed_at.date() >= frm]

    by_period: dict[tuple, list] = {}
    for t in trades:
        by_period.setdefault(_period_key(t.closed_at.date(), period), []).append(t)

    keys = _walk_periods(in_range[0].dt, in_range[-1].dt, period)

    equity = starting_capital
    rows = []
    for key in keys:
        bucket = by_period.get(key, [])
        pnl = round(sum(t.pnl for t in bucket), 2)
        opening = equity
        ret_pct = round(pnl / opening * 100, 2) if opening > 0 else 0.0
        equity = round(equity + pnl, 2)
        start, end = _period_bounds(key, period)
        wins = sum(1 for t in bucket if t.pnl > 0)
        rows.append({
            "period": _period_label(key, period),
            "start_date": str(start), "end_date": str(end),
            "trades": len(bucket), "wins": wins, "losses": len(bucket) - wins,
            "pnl": pnl,
            "return_pct": ret_pct,
            "opening_equity": round(opening, 2),
            "closing_equity": equity,
            "hit_target": ret_pct >= target_return_pct,
        })

    n = len(rows)
    traded = [r for r in rows if r["trades"]]
    profitable = [r for r in rows if r["return_pct"] > 0]
    hit = [r for r in rows if r["hit_target"]]
    returns = sorted(r["return_pct"] for r in rows)
    mid = n // 2
    median = 0.0 if not n else (returns[mid] if n % 2 else round((returns[mid - 1] + returns[mid]) / 2, 2))

    worst_streak = streak = 0
    for r in rows:
        streak = streak + 1 if r["return_pct"] < 0 else 0
        worst_streak = max(worst_streak, streak)

    return {
        "strategy": strategy,
        "strategy_label": STRATEGY_REGISTRY[strategy].label,
        "instrument": instrument, "underlying": underlying,
        "period": period, "years": years,
        "from_date": str(in_range[0].dt), "to_date": str(in_range[-1].dt),
        "starting_capital": starting_capital,
        "position_size_lots": position_size_lots,
        "target_return_pct": target_return_pct,
        "summary": {
            "total_periods": n,
            "periods_with_trades": len(traded),
            "periods_flat": n - len(traded),
            "periods_profitable": len(profitable),
            "periods_hit_target": len(hit),
            "pct_hit_target": round(len(hit) / n * 100, 1) if n else 0.0,
            "pct_profitable": round(len(profitable) / n * 100, 1) if n else 0.0,
            "avg_return_pct": round(sum(returns) / n, 2) if n else 0.0,
            "median_return_pct": median,
            "best_period_pct": max(returns) if n else 0.0,
            "worst_period_pct": min(returns) if n else 0.0,
            "max_consecutive_losing_periods": worst_streak,
            "final_equity": equity,
            "total_return_pct": round((equity - starting_capital) / starting_capital * 100, 2),
        },
        "rows": rows,
    }
