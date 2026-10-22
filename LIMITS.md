# Limits — how OpenCode Zen and Go meter you

What we know about the two billing products, and where each number can be
read from. Everything here was checked against the live sites and the
`opencode-models` data on **2026-10-12**; each section says how it was
verified. Prices and limits change without notice — treat the docs links as
the authority and this file as a reading of them.

OpenCode sells two things through one Console:

| product | shape | endpoint |
|---|---|---|
| **Zen** | AI gateway, pay per token from a prepaid balance | `https://opencode.ai/zen/v1/…` |
| **Go** | monthly subscription with per-model included usage | `https://opencode.ai/zen/go/v1/…` |

They share a workspace, a balance, and API keys. Go's subscription does not
replace Zen's balance: it draws on included usage first, and can fall back
to Zen credits afterwards.

---

## Zen

### Subscription model and cost

**No subscription.** Zen is metered: *"You are charged per request and you
can add credits to your account"* (docs page). You buy credits up front and
they are drawn down per token.

Billing mechanics, from the Console billing docs:

- Credits are **prepaid**, bought through Stripe (card, Link, Alipay, UPI,
  WeChat Pay), and cover the whole workspace.
- A processing fee of **4.6% + $0.31** is added on top — a $50 purchase
  costs $52.61 and credits the full $50.
- **Auto-recharge** can top up when the balance runs low.
- The balance **never goes negative**; paid requests fail with **HTTP 402**
  once it reaches zero.
- Console is free for any team size, no seat fees.

### Usage models on Zen

| model | what it means | caps |
|---|---|---|
| **Free models** | 13 models priced `Free` (Big Pickle, Space Bunny Free, LongCat 2.5 Preview Free, Step 5 Preview Free, Exo Free, MiMo-V2.6-Flash Free, MiMo-V2.5 Free, Ling 3.1 Flash Free, Ling 3.0 Flash Fin Free, Nemotron 3 Ultra Free, Nemotron 3.5 Lightning Free, Muse Spark 1.3 Contributor Free, Jev 1.13 Free) | **Unpublished.** They exist and are enforced server-side, but the Zen docs list no limit for them |
| **Paid models** | normal per-token pricing, no documented ceiling | none published |

Two things about Zen's free models are worth knowing:

1. **They only work from inside OpenCode.** Calling one directly returns
   `{"type":"FreeTierError"}` — *"OpenCode's free tier can only be used
   from within OpenCode"*. Verified live.
2. **Their limits are real but unpublished.** The server parses them from a
   deployment secret (`packages/console/core/src/subscription.ts`, shape
   `{ promoTokens, dailyRequests, dailyRequestsFallback, checkHeaders }`),
   and the Zen docs page contains no limit column and no occurrence of
   "allowance", "Unlimited" or "per 5 hours" in 109KB of text.

---

## Go

### Subscription model and cost

Two monthly plans, from the Go docs page:

| plan | price | included usage |
|---|---|---|
| **Go** | **$10/month** | "Lower-cost access to the models below" |
| **Go Plus** | **$40/month** | "Higher usage limits across the models below" |

**Token prices are identical on both plans** — the plans differ only in how
much usage each model gets. Go Plus is *not* a flat multiplier: per model it
is 2×, 3×, 4× or 8× the Go limit.

There are also workspace-wide spend ceilings, which are a *different*
mechanism from the per-model limits (source: the product definitions in the
Console web app, `packages/console` client):

| plan | per rolling 5h | per calendar week | per paid period |
|---|---|---|---|
| Go | $12 | $30 | $60 |
| Go Plus | $48 | $120 | $240 |

### Usage models on Go

**"Usage limits are defined as monthly dollar amounts."** Each model has a
monthly limit, and the shorter windows are fixed fractions of it:

| window | share of the model's monthly limit |
|---|---|
| 5-hour (rolling) | **20%** |
| weekly | **50%** |
| monthly | **100%** |

Limits range **$15–$60/month on Go** and **$60–$240/month on Go Plus**.

| usage model | examples | behaviour |
|---|---|---|
| **Free — no dollar allowance, but rate-limited** | LongCat 2.5 Preview Free, Step 5 Preview Free | the price table lists `Unlimited` in the **Monthly limit** column, i.e. they draw no included usage — it does **not** mean unthrottled (see below) |
| **Included in subscription** | everything else | drawn from the model's monthly dollar limit; token prices as published |
| **Beyond the limit** | — | requests are **blocked**… |
| **…unless "Use balance" is on** | — | Go **falls back to the Zen balance** and keeps going, spending credits |

`Unlimited` is the value in a *dollar* column, and the estimate table
repeats it because it derives from that column. It says nothing about rate
limits. Two pieces of evidence say the free models are throttled anyway:

- The Console has a distinct limit state, *"**Free limit reached** — Subscribe
  to OpenCode Go for reliable access to the best open-source models"*,
  separate from its *"Go limit reached"* dialog. A free-tier limit exists and
  triggers on its own.
- Both free models carry **identical model ids on Zen and Go**
  (`longcat-2.5-preview-free`, `step-5-preview-free`), so they are the same
  models behind the same free tier — whatever throttles them on Zen applies
  on Go.

The free-tier cap itself (`dailyRequests` in the server's `ZEN_LIMITS`) is
still unpublished, so the honest reading is "free, and rate-limited by an
unknown amount", not unlimited.

### Estimating the free tier's size

The cap is unpublished, but on 2026-10-12 this workspace ran into it live:
Space Bunny Free started failing after **662 requests in a day** (UTC), while
paid models kept working. That is one anchor point for the size of the free
bucket — and the 30-day usage export lets us turn it into relative costs per
free model, because cost per request varies enormously:

| free model | in / out / cache per req | rates $/1M | ≈ cost/req |
|---|---|---|---|
| step-5-preview-free | 15.7k / 624 / 279k | 0.96 / 2.74 / 0.048 | **$0.030** |
| longcat-2.5-preview-free | 5.6k / 498 / 185k | 0.30 / 1.20 / 0.006 | $0.0034 |
| mimo-v2.5-free | 11k / 589 / 68k | 0.16 / 0.32 / 0.004 | $0.0022 |
| mimo-v2.6-flash-free | 4.3k / 950 / 85k | 0.16 / 0.32 / 0.004 | $0.0013 |
| muse-spark-1.3-contributor-free | 2.3k / 375 / 55k | 0.10 / 0.20 / 0.002 | **$0.0004** |
| space-bunny-free | 4.1k / 515 / 141k | (no list price; small-model proxy) | $0.0014 |

Fresh 30d export (2026-10-21) adds big-pickle at 5.1k in / 756 out /
79k cache per req (no catalog list price, so no $/req imputation for it).
Quota attribution caveat: daily aggregates alone cannot identify the cap —
starting balances are unknown (a model can fail at low daily use on a
nearly-spent bucket), models share the bucket in parallel, and the export
resolves days, not the 5h windows the gate likely uses. The two anchor days
(space-bunny-free serving 2,809 req / 417M tokens on 2026-09-27, failing
at 662 req / 175M tokens on 2026-10-12) rule out only a fixed daily request
cap at or below ~3,000 — everything else needs paired quota+usage data
(see the run-to-cap experiment below). There is no live free-tier meter,
so the quota side of that pairing can only be observed at the failure
moment itself (exact error text + retry hints).

Token profiles are measured (this workspace, 30 trailing days, `/api/v2/usage/export`);
rates are the catalog's list prices — retail, not serving cost, so read the
rightmost column as ordinal, not exact. The spread is ~70× between Step-5-Free
and Muse-Spark-Contributor-Free. A pure request-count cap would let one Step-5
session cost what ~70 Muse Spark sessions do; a cost-based cap explains why
cheap models run longer, and the server shape carries a `promoTokens` field
next to `dailyRequests`, so a token bucket exists in the machinery.

Two caveats, both load-bearing:

- **2026-10-09 contradicts a shared daily bucket**: 1,161 free requests went
  through in a day (557 of them Step-5-Free). Either that day throttled too
  and nobody noticed, or the window is rolling rather than calendar-day, or
  the caps are per-model. The export is day-granularity, so intraday bursts
  are invisible — 662 Space Bunny requests in a few hours trips a rolling cap
  that 1,161 spread over a day never touches.
- **Space Bunny and Big Pickle have no catalog price**, so their $/req uses
  small-model proxy rates. Their traffic profiles are measured; their rates
  are assumed.

From the anchor, the tool derives nominal $/1M rates by normalising the
daily budget to **X = $0.10/day**: one Space-Bunny request ≈ $0.00015, and
every other free model scales by relative cost. Step-5-Free lands at
$0.019/1M (1.9¢), Muse-Spark-Contributor-Free at $0.0013/1M (0.13¢). All of
them sit below the cheapest paid rate ($0.034/1M), so free still ranks first
on value in the list view — but free tiers now get different score/$ instead
of sharing one price. The display still shows `0`, since the nominal is a
rationing estimate, not a bill.

The rates are **recomputed on every `fetch`, not committed**. `fetch` pulls
the trailing-30d usage export, aggregates per-model requests and token
volumes, prices each request at catalog rates, and writes
`data/usage/free_tier_nominals.json` — per-model traffic only, no user
columns ever reach the file. Models with fewer than 20 requests are left out
as noise and fall back to the anchor rate at load. The export needs a
read-only service-account key (`OPENCODE_API_KEY` in the environment); without
one, `fetch` says so and keeps the existing snapshot, and offline runs fall
back to the committed table in `pricing.py`, which is only ever the stale
stand-in. Re-running the derivation against the live export reproduces the
committed values to the digit, which is how you know the fallback is honest
rather than hand-tuned.

Why limits differ per model: *"For most models, we make this work through
bulk discounts and reserved GPU capacity… For some models, we haven't had
the opportunity to negotiate a discount or host them at a lower cost."*

### The "requests per 5 hours / week / month" tables are estimates

The Go docs also publish per-model request counts. They are **not** the
enforcement mechanism — the section is headed *"Estimated requests … based
on typical Go usage patterns"*, and each row is just the dollar limit
converted at typical token volumes. That is why `month ÷ 5h ≈ 5.0` for every
model: it is the 100%/20% rule above showing through.

Useful as a rough sense of which models are rationed — Grok 4.7 ≈ 845
requests/month against MiMo-V2.6-Flash ≈ 150,400 — but never as a limit to
enforce against.

---

## Where the data lives

### Public, no credentials

| source | what it gives |
|---|---|
| `https://opencode.ai/docs/zen/` | model list, endpoints, per-token prices. **No limits.** |
| `https://opencode.ai/docs/go/` | plans and prices, per-model monthly limits, the Go/Go Plus estimated-request tables |
| `https://opencode.ai/docs/console/billing/` | credits, the 4.6% + $0.31 fee, auto-recharge, invoices, the 402 behaviour |
| `https://opencode.ai/docs/console/api/` | the Console API overview — documents **only** Inference, Providers and Budgets |
| `https://opencode.ai/console/assets/index-*.js` | undocumented clients (see below) |
| `sst/opencode` → `packages/console/core/src/{subscription,quota,billing,lite}.ts` | the server's own limit shapes and enforcement logic |
| `https://opencode.ai/v2/llms.txt`, `/v2/openapi.json` | doc index; the CLI's 118-route HTTP surface |

The **billing API is undocumented** but present in the Console bundle, which
is how it was found: `GET /console/api/billing/{status,account,details,ledger,invoices,auto-recharge,payment-attempts}`.
`BillingStatus` carries `balanceMicroCents`, `creditLimitMicroCents`,
`availableMicroCents`, `canPurchaseCredits`, `canEnableAutoRecharge`. These
routes are **person-only** (see below).

### Go plan structure (agreed 2026-10-21; executable model: `/tmp/opencode/go_allowance.py`)

Two plans, Go $10/mo and Go Plus $40/mo. Token prices identical across
plans; per-model monthly dollar limits differ per plan (Go $15–$80, Plus
$60–$240) and are NOT proportional to the plan price — same $10 plan spans
$15 (kimi-k3) to $80 (deepseek-v4.1-flash), i.e. cheap models win twice
(low $/req times high $ cap: 490 vs 226,600 req/month, a 462× spread).
Windows are fractions of each model's monthly limit: 5h = 20%, weekly =
50%, monthly = 100%. Overage ("Extra usage" toggle) falls back to Zen
credits at the same token prices. Free rows are Unlimited: outside the
dollar windows entirely, and the page confirms they keep working after
priced limits are hit. `req/month = monthly_limit / $/req` with
`$/req` from the page's token assumptions reproduces the page's own
estimates (24/26 exact; kimi-k2.7-code deviates 26%, gemini-4-argon has no
published assumptions). Credit-burn ranking is plan-invariant ($/req does
not change with plan); only caps scale, non-uniformly (2–4×).
Open, with discriminating tests: whether per-model caps are silos (page
framing leans silo) or one shared pool — exhaust one model's monthly cap,
then try another priced model (silos: works; pool: blocked); and whether
the free throttle is global across models/vendors/plans — at a free
failure, check same-moment other-vendor free models (works: per-model or
vendor issue; all fail: global gate) and the same model via the other
plan's route, with exact error text (vendor 429s mimic the gate).

Live drift demo (2026-10-21): step-5-preview's Plus cap was $240 in the
Oct-19 tier snapshot and $60 on the page today — absorbed silently by the
ungated tier scraper, the exact failure the allowance gate exists to
catch. Authority split: the allowance file (`data/plans/go.json` +
shipped fallback) owns Go caps, token assumptions, estimates and tiered
prices with validation; `data/tier_prices/go_prices.json` stays the
ranking input in variant-row shape (same numbers, verified 33/33 on base
tiers — apparent mismatches are peak/threshold variants stored as separate
rows). Do not merge the two without the variant-selection logic; Zen has
no v2-style limits page, so its prices stay scrape-only.

### Go limits live on the page, not in any API (verified 2026-10-21)

Golden reference: `https://opencode.ai/v2/docs/console/go#usage-limits`. What it
has that no API carries: **per-model monthly dollar limits** (Go: $15–$80,
Plus: $60–$240, free rows Unlimited), the **window rule** (5h = 20% of the
monthly limit, weekly = 50%, monthly = 100%), per-model estimated request
counts, and the token-per-request assumptions behind them (heavy prompt
caching: ~55–89k cached tokens/req — Go-typical traffic, not transferable
to Zen, where we measure different profiles). Token prices are in both
places (page is authoritative incl. context-tier and peak/off-peak
variants); export `cost_micro_cents` verifies against page prices
(spot-check: muse-spark-1.3-contributor $10.91 vs $10.90 computed).
Per-model remaining therefore needs a snapshot of the page's limits table
as the denominator: `remaining = limit × {0.2, 0.5, 1.0} − export spend in
window`. The model list itself IS in an API (`/inference/go/v1/models`,
which the page documents).
The plan-level `percent` from `/zen/go/v1/usage` is opaque: with
(monthly 2%, weekly 4%) live, export-derived candidates give max-model
19.4%/47.1% and sum-of-Go-limits 1.0%/2.8% — neither reproduces it, so the
headline number cannot be independently derived. Per-model math from
page + export is more informative than the API percent. Workspace plan
(Go vs Plus, limits differ 2–4×) is not exposed by any endpoint.

### Exact metric shapes (verified live 2026-10-21, personal key, `https://opencode.ai` base)

**Zen — spend and credit balance (pay-per-use, deducted from balance):**

| endpoint | exact fields |
|---|---|
| `GET /console/api/v2/usage/export?range=7d\|30d\|90d` | CSV per UTC day × model: `day, user_type, user_id, user_name, provider, model, requests, input_tokens, output_tokens, cache_read_tokens, cache_write_5m_tokens, cache_write_1h_tokens, cost_micro_cents`. `provider` is `opencode` (Zen) or `opencode-go` (Go); free rows carry `cost_micro_cents = 0` |
| `GET /console/api/usage/summary` | lifetime workspace totals: `totalRequests, totalInputTokens, totalOutputTokens, totalCacheReadTokens, totalCacheWrite5mTokens, totalCacheWrite1hTokens, totalCostMicroCents, services[]` |
| `GET /console/api/usage/models` | per-model lifetime `items[]`: `model, provider, totalRequests, totalInputTokens, totalOutputTokens, totalCacheReadTokens, totalCacheWrite5mTokens, totalCacheWrite1hTokens, totalCostMicroCents` |
| `GET /console/api/usage/cost-by-day` | spend history back to Aug 10: `[{date, totalCostMicroCents, totalTokens, totalRequests}]` |
| `GET /console/api/billing/status` | the live credit meter: `billingMode, mode, balanceMicroCents, creditLimitMicroCents, availableMicroCents, canPurchaseCredits, canEnableAutoRecharge` (this workspace: ~$2.41 balance, no credit limit) |
| `GET /console/api/billing/account` | `orgId, creditLimitMicroCents, createdAt, updatedAt` — no usage |
| `GET /console/api/members` | `items[]`: `avatarUrl, createdAt, disabledAt, displayName, email, role, spentMicroCents, userId` — per-member spend (aggregate only; never persist emails) |

**Go — included-usage allowance (monthly subscription, per-model included amounts):**

| endpoint | exact fields |
|---|---|
| `GET /zen/go/v1/usage` | plan-level only: `{"usage": {rolling, weekly, monthly: {status, percent, resetsAt}}}`. No per-model split, no dollars, no free-tier dimension |
| `GET /zen/go/v1/limits`, `/zen/v1/usage`, `/inference/v1/usage` | **404** — do not exist |

Notes: budgets (`/api/v1|v2/budgets/members`, opt-in per-member caps — not the plan
allowance) return **403** with a personal key today (200 with a service key on
2026-10-12 — another token-class split). Go rows also appear inside the Zen
history endpoints (`provider = opencode-go`); with "Use balance" on, Go draws
past its included amounts from the Zen `balanceMicroCents` above.

### With a read-only service-account key (`oc_sk_…`, "All" permissions)

Verified live against a read-only key on 2026-10-12. Base is
`https://opencode.ai/console`.

| endpoint | status | what it gives |
|---|---|---|
| `GET /api/v2/usage/export?range=7d\|30d\|90d` | **200** | **the best source.** CSV: `day, user_type, user_id, provider, model, requests, input/output/cache tokens, cost_micro_cents` — per UTC day, per model, both Zen and Go |
| `GET /api/usage/summary` | **200** | workspace totals (requests, tokens, cost) |
| `GET /api/usage/models` | **200** | per-model lifetime requests, tokens and cost |
| `GET /api/usage/cost-by-day` | **200** | daily cost/tokens/requests, back to Aug 10 |
| `GET /api/v2/config` | **200** | the workspace's live model set with **tiered cost arrays** and context limits — richer than scraping the price tables |
| `GET /api/v1/budgets/members`, `/api/v2/budgets/members` | **200** | per-member spend against an **opt-in cap you set** (not the plan allowance) |
| `GET /api/members` | **200** | members, roles, `spentMicroCents` — **includes member emails** |
| `GET /api/v2/budget-policies` | **200** | empty in our workspace |

**Not reachable with a service-account key:**

| endpoint | status | why it matters |
|---|---|---|
| `/api/billing/*` (incl. `status`) | **403** | the only source of balance and `availableMicroCents`. Requires a signed-in person via the OAuth device flow (`/console/auth/device/code`, with `x-org-id`) |
| `/api/account/profile`, `/api/orgs` | 401 | person-only |
| `/api/internal/{traffic,go/last-hour,eligibility}` | 401 | person-only |
| `/api/v1/usage/export` | 403 | deprecated; only for workspaces not yet on v2 |
| inference calls (`zen/v1/chat/completions`) | 403 | *"This API key can't call models"* — service keys are not inference keys |
| Zen free models from any client | 403 | `FreeTierError` — free tier is locked to the OpenCode client |

### Recommendation

For anything about **usage**, use `/api/v2/usage/export` alone: one
endpoint, day-level granularity, both products, with request counts and
cost. Pair it with the public Go docs for limits and the Zen docs for prices.

Two things it cannot give you, and which should therefore never be printed
as if they were known:

- **Zen free-tier remaining** — the cap is in a server secret. Only relative
  load is measurable.
- **The 5-hour rolling window** — the export is one row per UTC day, so the
  weekly and monthly windows can be summed but a rolling 5-hour window
  cannot be reconstructed.

---

## Gotchas for the tool

1. **Our price parser reports the wrong plan's limit.** `pricing.py` keeps
   the *larger* of the Go and Go Plus limits for each model, so a Go
   subscriber ($10/month) is shown Go Plus limits — up to **8× too high**
   (GLM-5.3: $120 shown vs $15 actual). Both numbers are in the same table;
   the parser has to pick by plan, which means knowing the plan.
2. **Estimated requests are not limits.** They are the dollar cap converted
   at typical usage; the 5× ratio that looks like three separate budgets is
   just 20%/50%/100%.
3. **Local stats are not workspace stats.** `opencode stats --models`
   counts requests accurately (verified: ratio 1.00–1.02 against the server
   for models used only through this machine) but sees only this machine —
   for one free model it read 24 against the server's 9,853. Use it for
   "what did I use today", never for remaining allowance.
4. **Go overages may be invisible in Go's own numbers.** With "Use balance"
   enabled, requests past the limit are billed to Zen credits and keep
   flowing, so Go usage can exceed its cap without an error. The Console
   confirms this is the designed path — its *"Go limit reached"* dialog
   offers *"enable usage from your available balance"* as the remedy.
5. **A model's cost is a step function, not a number.** For a Go model with
   an allowance it is $0 until the cap and list price after it; for a free
   model it is $0 until an unpublished cap and then blocked or converted.
   That is why the list view should not rank Go's included models by
   score-per-list-price: the dollar it divides by is already spent, and the
   constraint that binds is the per-model allowance.
6. **The config API is not the availability record (v2 migration).**
   `GET /api/v2/config` now answers this workspace with a stale legacy set
   (all models `disabled: true`, `opencode-go.models` empty) while the
   inference gateway serves 37 models per flavor — verified 2026-10-21,
   same result for personal and service keys. Availability therefore comes
   from `GET /inference/{,go/}v1/models`; config only annotates.
7. **Go allowance windows have an undocumented endpoint.**
   `GET https://opencode.ai/zen/go/v1/usage` (Bearer key, not a stable
   public API) returns plan-level `usage.{rolling,weekly,monthly}` each
   with `status`, `percent` and `resetsAt` (monthly is
   subscription-anchored). Probed 2026-10-21 with a personal key; per-model
   usage stays the `usage/export` route. Displaying it is deliberately not
   implemented — see README Limitations.
   The response has **no per-model and no free-tier dimension** — it is a
   single plan-level percent per window, so free-model runway cannot be
   read off it (free rows carry an `Unlimited` monthly limit: they draw no
   included usage and are gated by unpublished rate limits instead). There
   is no `/zen/v1/usage` (404). Calls need a real client `User-Agent` —
   bare-urllib requests die at Cloudflare (1010). Go's credit fallback pool
   is separate: `GET /console/api/billing/status` (person-only) carries
   `balanceMicroCents`/`availableMicroCents` (this workspace: ~$2.41).