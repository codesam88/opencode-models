---
status: done
type: feature
priority: high
area: cli
---

## Notes

**Closed 2026-10-23**: all six build steps landed and merged (live gateway
availability, zero-Go warning, `models --all` with `[x]`/`[ ]`/`[?]`,
served-only `list`/`next`, README/SECURITY/LIMITS/CHANGELOG docs).
Verified against the tree: `fetch_served` is only ever called with an
authed transport (no anonymous-list fallback), `models --all` and the
zero-Go warning are in `cli.py`, CHANGELOG documents the change, and the
usage probe shape is in LIMITS.md. Full suite + lint green in CI.
One item not re-runnable here: the live key/no-key/wrong-key matrix
needs a valid personal key (the environment key 401s); worth one
maintainer pass with a good key if the gateway behavior is ever doubted.

Switch availability to the inference gateway's model lists. Discovery
journey (document as user guidance, per maintainer): first ran with **no
token** (views fell back to snapshots with a notice), then with an
**old/expired service-account token** (401s, snapshots), then found the
required token: a **personal API key from User → Account → Personal API
Keys — NOT Settings → Service accounts** (the settings-tab key class is
what misled the earlier session). Branch: `member-availability`.

### Verified endpoint matrix (2026-10-20/21, personal key unless noted)

| endpoint | no auth | garbage bearer | valid key |
|---|---|---|---|
| `GET /inference/v1/models` (zen) | 200 — 86 entries (catalog-wide) | 401 | **200 — 37 (workspace-scoped)** |
| `GET /inference/go/v1/models` (go) | 200 — 37 | 401 | **200 — 37** |
| `GET /api/v2/config` (org) | 401 | 401 | 200 but stale content |
| `GET /api/v2/config/members/:userId` | — | — | 200 (identical payload to org; 403 for service keys) |

Config content is stale server-side (go 29 → 0 and zen → all-disabled
within one day on Oct 20, same token both readings; matches the platform
revision). Inference lists are the live truth: presence = servable.

### Token classes (from V2 Console API docs)

| | personal key (Account → Personal API Keys) | service key (Settings → Service accounts) |
|---|---|---|
| acts as | **the member** — your role, budgets, model rules | non-human member, own budget |
| member config route | 200 | 403 (even All: "can't manage models") |
| inference lists | keyed, workspace-scoped | listed with Models:Read (untested here: no valid service key on hand) |

### Decisions

- **Availability source**: the two inference lists, always **with a
  valid key** — the anonymous zen list (86) over-promises, the keyed one
  (37) is this workspace's entitlements.
- **No token**: unchanged UX — snapshots + the existing first-line
  notice (`no API token found …`) on `list`/`next`; `models` shows its
  snapshot origin line. Never substitute the anonymous list.
- **Invalid/expired token (401)**: existing warning path (`live …
  unreachable (HTTP 401 …) — using the last snapshots`), then snapshots.
  HTTPError ⊂ OSError so the current catches hold.
- **Zero-Go warning (requirement)**: a keyed availability pull returning
  0 go slugs prints, wherever availability is resolved (`_current_offered`
  for list/next, `models` head): warn the models may not be enabled and
  the token should be a personal key with model permissions. Advisory,
  not an error — a workspace genuinely without Go gets the same line.
- **models view rows** (maintainer decision): default = served only;
  a new `--all` flag adds config-known unserved rows. Status column
  becomes a two-char marker: `[x]` served, `[ ]` known-not-served
  (`--all` only), `[?]` unknown (docs-snapshot-only offline). Rows are
  always intersected with the catalog (the zen served list contains
  `test`, `*-backup` junk).
- **list/next availability**: served set only — legacy config-only
  models drop out of the views (they are not callable).

### Usage info (research, then decide — do not build a display yet)

- New find via https://github.com/Mark1708/opencode-usage-monitor/pull/3
  ("Add OpenCode Go usage provider"): `GET https://opencode.ai/zen/go/v1/usage`,
  `Authorization: Bearer <key>` — **undocumented, not stable**. Rolling
  (5h)/weekly/monthly objects with `percent` + `resetsAt`; `status:
  "rate-limited"` or ≥100% = limit hit; missing windows = partial.
- Known already: `usage/export`, `usage/summary`, `usage/models`,
  `budgets/members` (v1, service-key friendly, carries member emails —
  aggregate only, never persist).
- Step: probe `/zen/go/v1/usage` live, record the shape in LIMITS.md,
  then present options — README Limitations currently states there is no
  allowance column; changing that is a separate, explicit decision.

## Steps

- [x] 1. `live.py`: availability from `fetch_served(get)` → {tier: set}
  over the two inference endpoints; keep config fetch only for the
  disabled annotation (union in `save_live`/`load_live`: slug → bool,
  True = served). RED tests first (served parse, 401 → propagates,
  snapshot round-trip with mixed status).
- [x] 2. Zero-Go warning at `_current_offered` + `models` head. RED
  first (keyed fetch yields go=0 → warning line contains the personal-key
  guidance; keyed go>0 → no warning; no-token → existing notice only).
- [x] 3. `models` view: `[x]`/`[ ]`/`[?]` marker column, served-only
  rows by default, new `--all` flag adds unserved, catalog
  intersection in both modes. RED first (marker rendering, --all rows,
  junk filtered).
- [x] 4. `list`/`next` filter on served-only; config-only legacy models
  disappear. RED first (a config-known/unserved model is excluded, a
  served model included).
- [x] 5. Docs: README (availability source + token guidance: personal
  key path User → Account → Personal API Keys), SECURITY (key classes,
  inference endpoints as network surface), LIMITS (config-stale finding
  + `/zen/go/v1/usage` if step 6 confirms), CHANGELOG.
- [x] 6. Usage probe: record `/zen/go/v1/usage` response shape in
  LIMITS.md; present display options to the maintainer (separate task
  to implement — README Limitations must not be edited before that
  decision).
- [ ] 7. Verification: full suite green; live matrix re-run (no/wrong/
  valid × models/list/next) incl. the zero-Go warning firing; width
  tripwires; `just lint`.

## Open decisions (settled at interview before building)

- models view: union display (recommended, keeps `disabled` meaningful)
  vs served-only rows (status column becomes constant).
- Usage endpoint: probe-then-present; no display work in this task.
- (settled, amended) models rows: served default (endpoint + name only), `--all` adds unserved rows plus the `Avail` (`[x]`/`[ ]`/`[?]`) column.

## Verification

- New tests RED before each implementation step; full suite green after.
- Live probes: valid key shows 37+37 and no warning; emptied go (mock
  in tests) shows the warning; no token keeps the historical notice
  wording (existing tests pin it).
- No anonymous-list fallback anywhere in code (grep for the inference
  URLs to confirm only `_api_get` callers).
