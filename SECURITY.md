# Security

## What the tool touches

`opencode-rank` is an offline-first console tool: every command except two
runs entirely on data committed in this repository.

| command | network | destination |
|---|---|---|
| `fetch` | yes, one sequential pass | `opencode.ai/data` model records, plus the Console tier docs for pricing snapshots |
| `scores --refresh` | yes | the Hugging Face `datasets-server` copy of the arena Elo table |
| everything else (`list`, `scores`, `benchmarks`, `conflicts`, `render`) | **no** | — |

No credentials, tokens, cookies or telemetry are involved, and nothing the
tool computes ever leaves the machine. There is no dependency tree to
supply-chain: the package is stdlib-only, so `uv tool install .` resolves
nothing at runtime.

## Scraper etiquette

`fetch` identifies itself with a real user agent (`opencode-rank/<version>`),
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
