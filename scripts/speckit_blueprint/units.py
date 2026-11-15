"""Units, formatted in one place so status, review.md, the dashboard and the status line agree:
2h 5m, 45m, 38s, 1d 4h (calendar), $3.10, 2.4M, 310k, ranges "45m – 1h 20m"."""

from __future__ import annotations


def duration(seconds: float | None, calendar: bool = False) -> str:
    if seconds is None:
        return "—"
    s = max(0, int(round(seconds)))
    days, rem = divmod(s, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h" if hours else f"{days}d"
    if hours:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    if minutes:
        return f"{minutes}m" if (calendar or not secs or minutes >= 10) else f"{minutes}m {secs}s"
    return f"{secs}s"


def cost(usd: float | None) -> str:
    if usd is None:
        return "—"
    return f"${usd:,.2f}" if usd >= 0.995 or usd == 0 else f"${usd:.2f}"


def tokens(n: float | None) -> str:
    if n is None:
        return "—"
    n = float(n)
    if n >= 1e6:
        return f"{n / 1e6:.1f}M"
    if n >= 1e3:
        return f"{n / 1e3:.0f}k"
    return f"{int(n)}"


def span(low, high, fmt=duration) -> str:
    """A range with both ends, or one value when they round to the same text."""
    a, b = fmt(low), fmt(high)
    return a if a == b else f"{a} – {b}"
