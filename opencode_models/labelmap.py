"""How each board's model labels map onto catalog ids.

A board and the catalog name the same models differently: Terminal-Bench
says "Opus 5.5" where the catalog says "Claude Opus 5.5", SWE-bench says
"Claude 3.5 Sonnet" where the catalog says "Claude Sonnet 3.5 v2". The token
matcher in :mod:`boards` handles most of that, but two things it cannot do
on its own:

- **Decide a tie.** "GPT-3.5" fits ``openai/gpt-3.5-turbo`` and
  ``openai/gpt-5.3-codex`` equally well; the right answer is the first, and
  no amount of token arithmetic says so.
- **Stay put.** The catalog gains and loses rows between fetches. A label
  that resolves today can become ambiguous after an unrelated model lands,
  and a board score would stop being applied without anyone noticing.

So each service's awkward labels are recorded here, once, by hand. This is
the *exception list*: labels the matcher places by name or by unique token
overlap are not listed, because there is nothing to decide. When one appears
here it is because a human looked at the candidates and made a call.

To add an entry: run ``opencode-models boards``-style analysis (see the
backfill-scores skill), find the label the matcher cannot place, look at its
candidates, and write down the one you mean. If the candidates are genuinely
different models, leave the label out — an absent label is dropped on
purpose, which is better than a wrong merge.
"""

from __future__ import annotations

#: Board source host → {label as the board writes it: catalog model id}.
#: Ids are the canonical ones; a dated snapshot beside them is reached
#: through :func:`~opencode_models.boards.match_models`, which returns every
#: row of the matched model.
LABEL_MAPS: dict[str, dict[str, str]] = {
    # Terminal-Bench drops the vendor prefix: "Opus 5.5", not "Claude".
    "www.tbench.ai": {
        "Fable 5": "anthropic/claude-fable-5",
        "Fable 5.1": "anthropic/claude-fable-5-1",
        "Opus 4.8": "anthropic/claude-opus-4-8",
        "Opus 5": "anthropic/claude-opus-5",
        "Opus 5.5": "anthropic/claude-opus-5-5",
        "Sonnet 5": "anthropic/claude-sonnet-5",
        "Sonnet 5.5": "anthropic/claude-sonnet-5-5",
    },
    # SWE-bench orders the version after the family, adds release tags, and
    # on one label ties with a newer model family entirely.
    "www.swebench.com": {
        "Claude 3.5 Sonnet": "anthropic/claude-3-5-sonnet-20241022",
        "Claude Sonnet 3.5": "anthropic/claude-3-5-sonnet-20241022",
        "claude-3-5-sonnet-20241022": "anthropic/claude-3-5-sonnet-20241022",
        "DevStral Small 2507": "mistral/devstral-small-2507",
        "Devstral (2512)": "mistral/devstral-2512",
        "Gemini 3 Pro": "google/gemini-3-pro-preview",
        "Llama 4 Maverick Instruct": "meta/llama-4-maverick-17b-instruct",
        "Llama 4 Scout Instruct": "meta/llama-4-scout-17b-instruct",
        # "GPT-3.5" fits gpt-3.5-turbo and gpt-5.3-codex equally by token
        # count; it means the turbo, which the 0.4% resolve rate confirms.
        "GPT-3.5": "openai/gpt-3.5-turbo",
    },
}

#: Labels a human looked at and left unmapped, with the reason. Kept so the
#: same question is not re-litigated on every fetch — an unmapped label
#: drops its score, which is the correct outcome when the board's label
#: genuinely fits several models.
UNRESOLVED: dict[str, dict[str, str]] = {
    "www.swebench.com": {
        "Gemini 3 Flash": "fits 3-flash-preview, 3.5, 3.6 and 3.7 alike; "
        "the board does not say which",
        "Kimi K2": "fits k2-instruct, k2-thinking, k2.5 and k2.6 alike",
    },
}


def mapped_id(source: str, label: str) -> str | None:
    """The catalog id *source* uses for *label*, if a human decided one."""
    host = _host(source)
    return LABEL_MAPS.get(host, {}).get(label)


def _host(source: str) -> str:
    """The host of a source URL, ignoring scheme and ``www.`` differences."""
    text = (source or "").split("://", 1)[-1]
    return text.split("/", 1)[0].lower()
