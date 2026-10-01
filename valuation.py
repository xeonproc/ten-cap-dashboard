"""Valuation formulas. Pure Python so the same module runs in CI and in the browser (stlite)."""

DEFAULT_HURDLE_RATE = 0.10
DEFAULT_GROWTH_RATE = 0.03


def normalized_eps(eps_values):
    """Average annual EPS over the supplied years."""
    values = [v for v in eps_values if v is not None]
    return sum(values) / len(values) if values else None


def intrinsic_value(norm_eps, r=DEFAULT_HURDLE_RATE, g=DEFAULT_GROWTH_RATE):
    """Normalized EPS * (1 + g) / (r - g). None when earnings are not positive or r <= g."""
    if norm_eps is None or norm_eps <= 0 or r <= g:
        return None
    return norm_eps * (1 + g) / (r - g)


def tbv_per_share(assets, liabilities, goodwill, intangibles, shares):
    if assets is None or liabilities is None or not shares:
        return None
    return (assets - liabilities - (goodwill or 0) - (intangibles or 0)) / shares


def discount_pct(price, intrinsic):
    """Margin of safety: positive when the stock trades below intrinsic value."""
    if price is None or not intrinsic:
        return None
    return (1 - price / intrinsic) * 100
