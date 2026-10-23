"""Plan pricing math and the usage-export endpoint.

Per-token plan rates live in the unified ``plans/`` snapshots (parsed by
``go_pricing``/``zen_pricing``); the catalog's single list price carries
no notion of Console tier — DeepSeek V4 Pro is $1.74 on Zen but $0.66
off-peak on Go. The representative cost here is a weighted mix of the
three token classes (20% input, 60% cached read, 20% output) rather than
the bare input rate: an agent re-sends the same context every turn, so
cached reads dominate the bill. Missing cached pricing means the provider
offers no cache discount, so those reads are billed at the input rate.
"""

from __future__ import annotations

#: Weight of each token class in the representative cost.
COST_WEIGHTS = {"input": 0.2, "cached": 0.6, "output": 0.2}


def weighted_cost(
    input_: float | None, output: float | None, cached_read: float | None
) -> float | None:
    """Representative $/1M across token classes (20/60/20).

    ``None`` input means we know nothing about this model. An absent cached
    rate means there is no cache discount (billed at the input rate); an
    absent output rate drops out and the remaining weights renormalise.
    """
    if input_ is None:
        return None
    cached = input_ if cached_read is None else cached_read
    parts = [(COST_WEIGHTS["input"], input_), (COST_WEIGHTS["cached"], cached)]
    if output is not None:
        parts.append((COST_WEIGHTS["output"], output))
    total_weight = sum(w for w, _ in parts)
    return sum(w * price for w, price in parts) / total_weight


#: Where the usage export lives: per-day, per-model traffic for the whole
#: workspace, both products. Person data never leaves the response — the
#: export below is aggregated in memory and only per-model numeric totals
#: are used.
USAGE_EXPORT_URL = "https://opencode.ai/console/api/v2/usage/export?range=30d"
