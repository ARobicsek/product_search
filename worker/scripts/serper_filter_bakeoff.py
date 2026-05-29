"""Phase 30 spike — filter-model bake-off (scratch; not wired into the pipeline).

Replays the EXACT production ai_filter prompt (captured by serper_filter_runtest
into data/serper_spike/<slug>.B.batch01.request.json) against any candidate
model, N times, and scores each against a hand-labeled GOLD verdict set:

  - determinism: mean pairwise Jaccard of the reject-index sets across trials
  - accuracy:    precision / recall / F1 of the PASS set vs gold
  - cost:        avg $/run from the repo's pricing table (when known)

Why this design: the prompt is the load-bearing artifact and is identical across
models, so a model's only job is to apply it. Gold is defined by the prompt's
OWN doctrine (e.g. "unknown -> pass"), so we measure prompt-adherence, not a
model's private world-knowledge.

Endpoints: Anthropic (native) + anything OpenAI-compatible via base_url
(GLM/DeepSeek/DeepInfra-Llama/Qwen/OpenRouter/OpenAI). A model is only run when
its key env var is present, so add keys to worker/.env and re-run.

    python scripts/serper_filter_bakeoff.py --slug ddr5-rdimm-256gb --trials 3 --temperature 0
"""

from __future__ import annotations

# ruff: noqa: E501  (scratch script; the model-registry lines are intentionally wide)
import argparse
import itertools
import json
import os
import time
from pathlib import Path
from typing import Any

from product_search.llm.pricing import estimate_cost_usd, format_cost_usd
from product_search.validators.ai_filter import _extract_json  # the real parser

WORK = Path(__file__).resolve().parent.parent / "data" / "serper_spike"

# Gold = correct verdict PER THE PROMPT'S STATED RULES (not external truth).
# DDR5: the description explicitly accepts 5600/6400 (downclock), speed_mts_min
# is >=4800, and "unknown -> pass". So the only correct rejects are the three
# listings whose titles self-identify as UDIMM/Unbuffered.
GOLD_REJECT: dict[str, set[int]] = {
    "ddr5-rdimm-256gb": {11, 20, 25},
}

# Candidate registry. kind: "anthropic" | "openai". A model runs only if key_env is set.
MODELS: list[dict[str, Any]] = [
    {"name": "haiku-4.5",     "kind": "anthropic", "model": "claude-haiku-4-5", "key_env": "ANTHROPIC_API_KEY"},
    {"name": "glm-4.6",       "kind": "openai", "model": "glm-4.6",            "base": "https://open.bigmodel.cn/api/paas/v4", "key_env": "GLM_API_KEY",      "price": ("glm", "glm-4.6")},
    {"name": "deepseek",      "kind": "openai", "model": "deepseek-chat",      "base": "https://api.deepseek.com",            "key_env": "DEEPSEEK_API_KEY"},
    {"name": "gpt-4o-mini",   "kind": "openai", "model": "gpt-4o-mini",        "base": None,                                  "key_env": "OPENAI_API_KEY"},
    {"name": "llama-3.3-70b", "kind": "openai", "model": "meta-llama/Meta-Llama-3.3-70B-Instruct", "base": "https://api.deepinfra.com/v1/openai", "key_env": "DEEPINFRA_API_KEY"},
    {"name": "qwen2.5-72b",   "kind": "openai", "model": "Qwen/Qwen2.5-72B-Instruct",              "base": "https://api.deepinfra.com/v1/openai", "key_env": "DEEPINFRA_API_KEY"},
]


def call_model(spec: dict[str, Any], system: str, user: str, temperature: float, max_tokens: int = 16384):
    """Return (text, in_tok, out_tok). Temperature is set explicitly here (the
    production ai_filter does NOT set it — testing that is half the point)."""
    if spec["kind"] == "anthropic":
        import anthropic
        client = anthropic.Anthropic(api_key=os.environ[spec["key_env"]])
        r = client.messages.create(model=spec["model"], system=system, max_tokens=max_tokens,
                                    temperature=temperature,
                                    messages=[{"role": "user", "content": user}])
        text = next((b.text for b in r.content if hasattr(b, "text")), "")
        return text, (r.usage.input_tokens if r.usage else 0), (r.usage.output_tokens if r.usage else 0)
    import openai
    client = openai.OpenAI(api_key=os.environ[spec["key_env"]], base_url=spec.get("base"))
    r = client.chat.completions.create(model=spec["model"], max_tokens=max_tokens, temperature=temperature,
                                        response_format={"type": "json_object"},
                                        messages=[{"role": "system", "content": system},
                                                  {"role": "user", "content": user}])
    msg = r.choices[0].message
    text = msg.content or getattr(msg, "reasoning_content", "") or ""
    u = r.usage
    return text, (u.prompt_tokens if u else 0), (u.completion_tokens if u else 0)


def reject_set(text: str, n: int) -> set[int] | None:
    parsed = _extract_json((text or "").strip().removeprefix("```json").removeprefix("```").removesuffix("```"))
    evals = parsed.get("evaluations") if isinstance(parsed, dict) else (parsed if isinstance(parsed, list) else None)
    if not isinstance(evals, list):
        return None
    rej = set()
    seen = set()
    for e in evals:
        if isinstance(e, dict) and isinstance(e.get("index"), int):
            seen.add(e["index"])
            if not e.get("pass"):
                rej.add(e["index"])
    return rej if len(seen) >= n * 0.8 else None  # require most items judged


def jaccard(sets: list[set[int]]) -> float:
    pairs = list(itertools.combinations(sets, 2))
    if not pairs:
        return 1.0
    vals = []
    for a, b in pairs:
        u = a | b
        vals.append(1.0 if not u else len(a & b) / len(u))
    return sum(vals) / len(vals)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", default="ddr5-rdimm-256gb")
    ap.add_argument("--trials", type=int, default=3)
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--show-rejects", action="store_true", help="Print the last trial's rejected titles.")
    args = ap.parse_args()

    req = json.loads((WORK / f"{args.slug}.B.batch01.request.json").read_text())
    system = req["system"]
    user = json.dumps(req["payload"], indent=2)
    n = len(req["payload"])
    gold = GOLD_REJECT.get(args.slug, set())
    gold_pass = set(range(n)) - gold

    print(f"slug={args.slug}  n={n}  trials={args.trials}  temp={args.temperature}  gold_reject={sorted(gold)}\n")
    print(f"{'model':<15} {'det(Jac)':>8} {'P':>5} {'R':>5} {'F1':>5}  {'~$/run':>8}  pass-counts / notes")
    print("-" * 100)
    for spec in MODELS:
        if not os.environ.get(spec["key_env"]):
            print(f"{spec['name']:<15} {'—':>8} (skipped: ${spec['key_env']} not set)")
            continue
        rej_sets: list[set[int]] = []
        counts: list[int] = []
        costs: list[float] = []
        notes = ""
        for _ in range(args.trials):
            try:
                text, itok, otok = call_model(spec, system, user, args.temperature)
            except Exception as e:
                notes = f"ERR {type(e).__name__}: {str(e)[:60]}"
                break
            rs = reject_set(text, n)
            if rs is None:
                notes = "unparseable/partial response"
                continue
            rej_sets.append(rs)
            counts.append(n - len(rs))
            price_key = spec.get("price")
            if price_key:
                c = estimate_cost_usd(price_key[0], price_key[1], itok, otok)
                if c:
                    costs.append(c)
            time.sleep(0.5)
        if not rej_sets:
            print(f"{spec['name']:<15} {'—':>8} {notes}")
            continue
        det = jaccard(rej_sets)
        # accuracy vs gold on the LAST trial's pass-set (representative)
        last_pass = set(range(n)) - rej_sets[-1]
        tp = len(last_pass & gold_pass)
        fp = len(last_pass - gold_pass)
        fn = len(gold_pass - last_pass)
        P = tp / (tp + fp) if tp + fp else 0.0
        R = tp / (tp + fn) if tp + fn else 0.0
        F1 = 2 * P * R / (P + R) if P + R else 0.0
        cost = format_cost_usd(sum(costs) / len(costs)) if costs else "n/a"
        print(f"{spec['name']:<15} {det:>8.2f} {P:>5.2f} {R:>5.2f} {F1:>5.2f}  {cost:>8}  counts={counts} {notes}")
        if args.show_rejects:
            for i in sorted(rej_sets[-1]):
                print(f"      reject #{i}: {req['payload'][i]['title'][:80]}")


if __name__ == "__main__":
    main()
