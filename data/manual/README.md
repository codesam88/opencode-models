# Manual catalog — tracked model records not present upstream

One JSON per model, named like the upstream record files
(`<lab>__<model>.json`), holding a **full** model record for a model the
upstream registry never listed but which the plans/tier docs offer. This is
the only place a record can be created by hand; `data/overrides/` can only
layer onto an existing record.

```json
{
  "model": {
    "id": "recursive/flint-40b-free",
    "name": "Flint 40B Free",
    "lab": "recursive",
    "priceUsdPer1M": {"input": 0.0, "output": 0.0, "cachedInput": 0.0},
    "benchmarks": [
      {"name": "Terminal-Bench", "version": "2.1", "score": 54.9,
       "metric": "success rate",
       "source": "https://huggingface.co/RecursiveSI/flint",
       "note": "vendor-reported on the model card"}
    ],
    "note": "Provenance: https://huggingface.co/RecursiveSI/flint"
  }
}
```

Rules:

1. **Every record carries `id`, `name`, `lab` and prices**
   (`priceUsdPer1M.input/output`; `cachedInput` may be null and bills
   at the input rate). A record missing any of these is refused and
   never rendered.
2. **Every record must be docs-documented**: its id must appear in a
   shipped plan snapshot or tier price doc. Invented models are not
   allowed.
3. **Every benchmark carries a `source` URL** (same rule as overrides).
   A record with zero benchmarks but documented prices is acceptable and
   renders as an unscored row (`·`); a record with neither prices nor
   benchmarks is not — skip it.
4. **Upstream wins on conflict**: if `data/opencode/` ever gains a record
   with the same id, the manual file is shadowed and reported as a manual
   orphan (`opencode_models overrides` / `fetch`), never silently applied.
5. **Curated overrides still layer on top**: a manual id can be overridden
   by an override file keyed on the same `model.id`, using the same
   filename convention (`<lab>__<model>.json`).
6. `fetch` writes only the upstream records; nothing ever writes here.
   `note` (top-level on `model`) carries the provenance URL justifying
   the record.
