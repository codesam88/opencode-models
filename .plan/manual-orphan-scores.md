---
status: done
type: feature
priority: high
area: backend
---

## Notes

Four orphan overrides name models upstream never carried; each pairs its
pinned axis with a second (board TB 4.0 via labelmap/token match, or a
second pinned cell), so a manual base record turns them from orphans
into scored rows. Manual README rule 5 is the pattern: minimal manual
base (id/name/lab/prices), benchmarks stay in overrides/.

- anthropic/claude-fable-5-1: TB 2.1 91.4 + DeepSWE 1.1 67.4 (override) + board TB 4.0 57.88
- anthropic/claude-opus-5: TB 2.1 89.1 (override) + board TB 4.0 53.94 (+ DeepSWE 74.0 StepFun/Minimax-agreeing, unpinned)
- openai/gpt-6-astra: TB 2.1 88.4 (override) + board TB 4.0 58.18
- minimax/minimax-m3.1-flash: DeepSWE 1.1 73.7 (override) + TB 4.0 38.1 (MiniMax table, added to override); override id lowercased to the roster slug

Singles with no pairing (qwen3.8-flash-next, qwen3.8-max-0902, ling-3.0-flash-fin, nemotron super-vl) stay orphans: documented, no score possible. New-vendor columns in the MiniMax table (DeepSeek V4.1s, GLM-5.3, GPT-5.6 Sol) need fresh curation, out of scope.

Seed already syncs manual/ like overrides/ (ManualDeployTest); no code change needed there.
