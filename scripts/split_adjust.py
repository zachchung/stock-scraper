#!/usr/bin/env python3
"""Split-factor normalization for historical annual EPS.

EDGAR/yfinance restate historical EPS on mixed split bases: some fiscal years
are as-filed (pre-split), others are restated to a post-split basis, depending
on which filing each value came from. This creates artificial YoY "drops" at
split boundaries (e.g. AAPL shows 2011 EPS $27.68 then 2012 EPS $6.31 purely
because 2012 was restated for the 7:1 split while 2011 was not).

The basis of each year is detected via implied shares = net_income / diluted_eps:
shares are roughly constant within a basis regime, and jump by exactly a split
factor at a basis change. Walking newest -> oldest, when shares jump by a known
split factor, all earlier years are divided by that factor (cumulatively).

Both scripts/eps_growth.py and scripts/stock_research.py use this module.
"""


def _match_split(ratio, split_factors):
    """Find the split factor f (new shares per 1 old) that best explains a
    jump in implied shares. Prefers splits actually recorded in the local
    corporate_actions table, falling back to generic integer factors."""
    candidates = []
    for f in sorted(set(split_factors), reverse=True):
        if f > 1 and abs(ratio - f) / f < 0.15:
            candidates.append(f)
    if candidates:
        return min(candidates, key=lambda f: abs(ratio - f))
    for f in range(2, 17):
        if abs(ratio - f) / f < 0.15:
            return f
    return None


def compute_split_factors(dates, eps, net_income, split_factors):
    """Return a list (same order as inputs) of divisors that convert each
    year's diluted EPS to the current (most recent year's) split basis.

    - dates:        fiscal year-end dates (chronological)
    - eps:          diluted EPS per year (None for missing)
    - net_income:   net income per year (None for missing)
    - split_factors: split factors recorded in corporate_actions, e.g. [7.0, 4.0]
    """
    n = len(dates)
    div = [1.0] * n
    implied = [None] * n
    for i in range(n):
        if eps[i] and net_income[i]:
            implied[i] = net_income[i] / eps[i]

    for i in range(n - 2, -1, -1):
        div[i] = div[i + 1]
        if implied[i] is None or implied[i + 1] is None:
            continue
        if implied[i] <= 0 or implied[i + 1] <= 0:
            continue
        ratio = implied[i + 1] / implied[i]
        f = _match_split(ratio, split_factors)
        if f is not None:
            div[i] = div[i + 1] * f
    return div


def adjusted_eps(dates, eps, net_income, split_factors):
    """Convenience wrapper: return split-adjusted EPS in the same order as input."""
    factors = compute_split_factors(dates, eps, net_income, split_factors)
    return [e / f if e is not None else None for e, f in zip(eps, factors)]


def split_factors_from_rows(split_rows):
    """Extract the split-factor list from corporate_actions rows
    (each row is (date, split_factor))."""
    return [r[1] for r in split_rows if r[1] and r[1] > 1]