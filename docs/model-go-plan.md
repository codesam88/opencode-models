# Go plan price model

Mental model of the OpenCode Go allowance system: what is confirmed,
what is assumed, and the exact test that would settle each assumption.
Golden reference: `https://opencode.ai/v2/docs/console/go#usage-limits`
(page numbers below are from its Go $10/mo tab unless noted; Plus $40/mo
scales caps 2–4× per model, non-uniformly).

## 1. Structure (all page-confirmed)

- Two plans: **Go $10/mo**, **Go Plus $40/mo**. Token prices are identical
  across plans; only the monthly caps differ.
- Each priced model has a **monthly dollar limit** `L(m)` ($15–$80 Go,
  $60–$240 Plus). Usage counts in real model cost against that model's cap.
- Allowance windows are fractions of the monthly limit: 5h = 20%,
  weekly = 50%, monthly = 100%.
- **Free models are Unlimited**: outside the dollar windows entirely.
  Reaching a priced limit does not block them (page: *"If you reach the
  usage limit, you can continue using the free models."*).
- **Overage** ("Extra usage" toggle): past the caps, Go draws Zen credits
  at the same token prices instead of blocking.
- Model list and IDs are live in an API (`/inference/go/v1/models`).
  Nothing else numeric on the page is: limits, windows, request estimates
  and token assumptions are **page-only** and may change.

## 2. Allowance equations

For model `m` with page token prices `p` ($/1M) and assumed tokens/req `t`:

```
cpr(m) = (t_in·p_in + t_cache·p_cached + t_out·p_out) / 10^6        ($/req)
R_month(m) = L(m) / cpr(m)                                         (req/month)
R_5h(m) = 0.2 · R_month(m),   R_week(m) = 0.5 · R_month(m)
remaining(m, w) = f_w · L(m) − S(m, w)      (f in {0.2, 0.5, 1.0}; S = export $ spend in window)
```

Validation: `R_month` reproduces the page's own request estimates 24/26
exactly (validator: `/tmp/opencode/go_allowance.py`). Flags: kimi-k2.7-code
deviates 26% (page 6,750 vs computed 4,968 — page-internal inconsistency,
use the computed value); gemini-4-argon-contributor has no published token
assumptions (no validation possible).

Measured (export, trailing 30d) cost is priced at page rates (spot-check
muse-spark-1.3-contributor: $10.91 vs $10.90 computed), so `S(m, w)` comes
straight from `/console/api/v2/usage/export`.

## 3. Worked examples (Go plan)

| model | $/req | L ($/mo) | req/mo | req/5h |
|---|---|---|---|---|
| muse-spark-1.3-contributor | 0.00026 | 60 | 226,600 | 45,317 |
| mimo-v2.6-flash | 0.00040 | 60 | 150,400 | 30,075 |
| step-5-preview | 0.00704 | 15 | 2,130 | 426 |
| kimi-k3 | 0.03060 | 15 | 490 | 98 |

Cheap models win twice — low `cpr` times high `L`: muse-spark serves
462× the requests of kimi-k3 under the same $10 plan. The page's stated
mechanism: bulk discounts and reserved capacity per model.

## 4. Allowance Model Hypotheses / Assumptions

- **H1 — per-model priced allowances, not plan-flat division (VERIFIED).**
  Same $10 plan spans $15–$80 caps; the 462× request spread above is the
  structure, not noise. No further test needed.
- **H2 — per-model caps are silos, not one shared pool (OPEN).** Page
  framing leans silo ("*each model* has the following usage limits"),
  but workspace data fits both (max observed: 47% of one model's monthly;
  ~$29 total). Test: exhaust one model's monthly cap, then try another
  priced model — works means silos, blocked means pool. Until then, never
  assume fungibility across models.
- **H3 — free models keep working after priced caps (CONFIRMED).**
  Page text, verbatim. Mechanism: Unlimited rows never enter §2's math.
- **H4 — free throttling is global across models/vendors/plans (OPEN).**
  Confounders: vendor-side 429s mimic the gate; Zen and Go free routes
  show different traffic profiles, so one throttle covering both is not
  established. Test at a free failure: (a) try a different-vendor free
  model the same minute — works means per-model quota or vendor issue,
  all-fail means global gate; (b) retry the same model via the other
  plan's route — separates Go-throttle from Zen-throttle; (c) keep the
  exact error text and retry hints.

## 5. Recommendation rule

- **Within included usage:** the binding constraint is per-model
  req/window headroom (`remaining(m, w) / cpr(m)` requests left). Rank by
  benchmark score subject to headroom > 0; headroom needs the export
  because the API's plan-level `percent` is opaque (live 2%/4% matches
  neither max-model 47.1%/19.4% nor spend-vs-sum-of-limits 2.8%/1.0%).
- **Beyond included (credits):** rank by **`$/req` only** — the credit
  table in §3's order. It is plan-invariant (same token prices both
  plans); only caps scale.

## 6. Open questions

- Workspace plan (Go vs Plus) is exposed by no endpoint; caps differ
  2–4× per model, so headroom math needs the plan as an input.
- Page numbers drift ("limits may change"): re-snapshot the limits table
  on the same discipline as tier-price snapshots before trusting `L(m)`.

## 7. Live verification (tests-only tooling)

The console never flags these on live runs — a warning that fires on
noisy live data reads as a bug report, so the checks live as pure
functions with hermetic tests (`check_export_prices`,
`check_caps_resolve`, `compare_window_percent` in
`opencode_models/allowance.py`, pinned by `tests/test_allowance.py`).

To run the live matrix by hand: (1) keyed `usage` for the overview;
(2) save the export CSV the run polled; (3) feed its rows plus the
current `data/plans/go.json` records through the three checks with the
publish-gate tolerance (10%); (4) record the outcome here. H2/H4 flip
to VERIFIED/CONFIRMED only on their burn experiments (§4), not on a
clean matrix run — absence of mismatch is not proof of the mechanism.
