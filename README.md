# model-pareto

Price/score Pareto frontier of AI models, from the
[opencode.ai/data](https://opencode.ai/data) catalog.

For a chosen benchmark, the tool plots every priced model's score against its
input price (log scale) and lists the **Pareto frontier**: models where no
other model is both cheaper and scores higher. Frontier membership is the
claim; everything else is context.

## Usage

```sh
python3 -m modelpareto fetch                     # refresh data/ from opencode.ai
python3 -m modelpareto benchmarks                # list benchmarks + coverage
python3 -m modelpareto render                    # pareto.html, best-covered benchmark
python3 -m modelpareto render --benchmark "SWE-Bench Verified" --out swe.html
```

`render` defaults to the benchmark covering the most models. The output is one
self-contained HTML file (inline SVG, no external assets) — open it from disk
or serve the directory.

Stdlib-only, Python ≥ 3.10, no dependencies. Tests: `python3 -m unittest discover -s tests`.

## Data

`data/` holds one JSON per model, copied verbatim from
`https://opencode.ai/data/<lab>/<model>.json`. Parsing is tolerant: models
without pricing or without the selected benchmark are simply excluded from the
frontier rather than failing a render.

## Caveats

- Prices are list prices from one catalog (OpenCode's); they can lag provider
  price changes.
- Benchmarks are heterogeneous (metric, harness, conditions differ per source).
  Compare within one benchmark, not across.
- Frontier membership depends entirely on which models the catalog covers.
