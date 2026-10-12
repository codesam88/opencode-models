# Limits — how OpenCode Zen and Go meter you

What we know about the two billing products, and where each number can be
read from. Everything here was checked against the live sites and the
`opencode-rank` data on **2026-10-12**; each section says how it was
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
| **Free, unlimited** | LongCat 2.5 Preview Free, Step 5 Preview Free | listed `Unlimited limited time` in the price table and `Unlimited` in all three windows — a promotion with an end date, not a permanent guarantee |
| **Included in subscription** | everything else | drawn from the model's monthly dollar limit; token prices as published |
| **Beyond the limit** | — | requests are **blocked**… |
| **…unless "Use balance" is on** | — | Go **falls back to the Zen balance** and keeps going, spending credits |

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
   flowing, so Go usage can exceed its cap without an error.