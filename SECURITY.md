# Security

## What the tool touches

`opencode-models` is an offline-first console tool: everything except the
rows marked yes below runs entirely on data already on disk.

| command | network | destination |
|---|---|---|
| `fetch` | yes, one sequential pass | `opencode.ai/data` model records, the Console tier docs for pricing snapshots, the Terminal-Bench 4.0 board page (tbench.ai), swebench.com's leaderboard, and — with a key — the Console config API (live model set) plus the usage export (free-tier nominals) |
| `models` | yes with a key, else no | the Console config API (live model set); the last snapshots offline |
| `list`, `next` | yes with a key, else no | the Console config API (live model set, recorded on the way) |
| `auth` | yes, one verification call | the Console config API (key validity check only) |
| everything else (`scores`, `overrides`, `conflicts`) | **no** | — |

## The service-account key

Key-gated commands read `OPENCODE_API_KEY` from the environment first
and fall back to `~/.config/opencode-models/api_key`. `auth` writes that
file: the key is prompted without echo, never printed back, and the
file is created (or tightened) to owner-only access (`0600`). The key
travels only as an `Authorization: Bearer` header to `opencode.ai` —
every request carries the `opencode-models` user agent and a 30-second
timeout — and it is never written to fetched data, logs, or output.

No cookies or telemetry are involved, and nothing the tool computes
ever leaves the machine. There is no dependency tree to supply-chain:
the package is stdlib-only, so `uv tool install .` resolves nothing at
runtime. Developer tooling (the ruff linter/formatter) lives in the locked
`dev` dependency group and never ships in the installed tool.

## Scraper etiquette

`fetch` identifies itself with a real user agent (`opencode-models/<version>`),
uses a 30-second timeout per request, and walks the sitemap once rather than
probing. Run it rarely; the data directory is the cache.

## Reporting

Please report suspected vulnerabilities through GitHub private security
advisories on the repository rather than in a public issue.

## Data provenance

`data/opencode/`, `data/aiarena/`, `data/tier_prices/` and the sources cited
in `data/overrides/` are third-party facts republished here for comparison,
with the source URL recorded next to every score. They are not relics of a
warranty: check the upstream page before quoting a number, and prefer the
independent (tier 1) runs the console labels as such.
