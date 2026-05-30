"""Phase 30 spike — filter-model bake-off (scratch; not wired into the pipeline).

# ruff: noqa: E501  (scratch script; the model-registry / gold lines are intentionally wide)

Runs the EXACT production ai_filter prompt against each candidate model, N times,
and scores against a hand-labeled GOLD verdict set:

  - determinism: mean pairwise Jaccard of the reject-index sets across trials
  - accuracy:    precision / recall / F1 of the PASS set vs gold
  - cost:        avg $/run from the local PRICES table

Self-sufficient: it rebuilds each product's prompt from the COMMITTED Serper
fixture + fixture profile (the data/serper_spike dump dir is gitignored), so a
fresh container needs nothing but the API keys in worker/.env.

The prompt is the load-bearing artifact and is identical across models, so a
model's only job is to apply it. Gold is defined per the prompt's OWN doctrine
(e.g. "unknown -> pass"); DDR5 + book gold are unambiguous, DJI + subscription
encode a strict target-SKU / in-scope reading (itself the ADR-117 product
decision — documented, not hidden).

Usage:
    python scripts/serper_filter_bakeoff.py --all                 # every slug x every keyed model
    python scripts/serper_filter_bakeoff.py --slug ddr5-rdimm-256gb --trials 3 --temperature 0 --show-rejects
"""

# ruff: noqa: E501,E702  (scratch script; wide registry/gold lines + terse metric one-liners)
from __future__ import annotations

import argparse
import itertools
import json
import os
from pathlib import Path
from typing import Any

from product_search.models import Listing
from product_search.profile import load_profile
from product_search.validators.ai_filter import _extract_json  # the real parser

WORK = Path(__file__).resolve().parent.parent / "data" / "serper_spike"
FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "serper"
PROFILES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "profiles"

# slug -> committed Serper fixture filename
SLUG_FIXTURE: dict[str, str] = {
    "ddr5-rdimm-256gb": "ddr5_rdimm_ecc_32gb.json",
    "dji-neo-2-motion-fly-more-combo": "dji_neo2_fly_more_combo.json",
    "the-week-1yr-subscription": "the_week_1yr_subscription.json",
    "the-netanyahus-joshua-cohen": "the_netanyahus.json",
}

# GOLD = correct PASS-set per the prompt's stated rules. Stored as pass-sets;
# everything else in 0..n-1 is a correct reject. DDR5 + book are unambiguous;
# DJI + subscription encode the strict target-SKU / in-scope reading (ADR-117).
GOLD_PASS: dict[str, set[int]] = {
    # only the 3 self-identifying UDIMM/Unbuffered titles are rejects
    "ddr5-rdimm-256gb": set(range(40)) - {11, 20, 25},
    # strict "Motion Fly More Combo" reading (both Haiku & GLM converged here)
    "dji-neo-2-motion-fly-more-combo": {1, 3, 4, 5, 6, 9, 12, 13, 14},
    # 1-year The Week print/print+digital; drops Junior/Newsweek/India/single/PDF + digital-only/quarterly
    "the-week-1yr-subscription": {1, 2, 4, 5, 9, 12, 13, 16},
    # 18 genuine new "The Netanyahus by Joshua Cohen"; drops [Used] + wrong-title books
    "the-netanyahus-joshua-cohen": set(range(33)) - {2, 4, 6, 11, 13, 14, 17, 21, 23, 25, 26, 28, 30, 31, 32},
}

# Local price table (USD per 1M tokens). Avoids touching production pricing.py.
PRICES: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.0, 5.0),
    "glm-4.6": (0.6, 2.2),
    "gpt-4o-mini": (0.15, 0.60),
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "deepseek-chat": (0.14, 0.28),
    "meta-llama/Meta-Llama-3.3-70B-Instruct": (0.10, 0.316),
    "Qwen/Qwen2.5-72B-Instruct": (0.36, 0.40),
}

# Candidate registry. kind: anthropic | openai | gemini. Runs only if key_env is set.
# NOTE: Gemini is routed through its OpenAI-COMPATIBLE REST endpoint (kind=openai,
# generativelanguage.googleapis.com/.../openai/), NOT the google-generativeai gRPC SDK:
# this container does TLS interception and the gRPC transport rejects the self-signed CA
# chain (endless CERTIFICATE_VERIFY_FAILED handshakes -> timeout). The "gemini" kind branch
# below is kept for environments without TLS interception, but is unused here.
MODELS: list[dict[str, Any]] = [
    {"name": "haiku-4.5",     "kind": "anthropic", "model": "claude-haiku-4-5",          "key_env": "ANTHROPIC_API_KEY"},
    {"name": "glm-4.6",       "kind": "openai",    "model": "glm-4.6",                   "base": "https://open.bigmodel.cn/api/paas/v4", "key_env": "GLM_API_KEY"},
    {"name": "gpt-4o-mini",   "kind": "openai",    "model": "gpt-4o-mini",               "base": None,                                   "key_env": "OPENAI_API_KEY"},
    {"name": "gemini-2.5-fl", "kind": "openai",    "model": "gemini-2.5-flash-lite",     "base": "https://generativelanguage.googleapis.com/v1beta/openai/", "key_env": "GEMINI_API_KEY"},
    {"name": "deepseek",      "kind": "openai",    "model": "deepseek-chat",             "base": "https://api.deepseek.com",             "key_env": "DEEPSEEK_API_KEY"},
    {"name": "llama-3.3-70b", "kind": "openai",    "model": "meta-llama/Meta-Llama-3.3-70B-Instruct", "base": "https://api.deepinfra.com/v1/openai", "key_env": "DEEPINFRA_API_KEY"},
    {"name": "qwen2.5-72b",   "kind": "openai",    "model": "Qwen/Qwen2.5-72B-Instruct",              "base": "https://api.deepinfra.com/v1/openai", "key_env": "DEEPINFRA_API_KEY"},
]


def _price(model: str, itok: int, otok: int) -> float | None:
    p = PRICES.get(model)
    return None if p is None else (itok or 0) * p[0] / 1e6 + (otok or 0) * p[1] / 1e6


def adapt(result: dict[str, Any]) -> Listing:
    """Serper result -> Listing (title-only; attrs empty; url normalized off the google redirect)."""
    import re
    from datetime import UTC, datetime
    raw = result.get("price")
    price = None
    if isinstance(raw, (int, float)):
        price = float(raw)
    elif raw:
        m = re.search(r"[-+]?\d[\d,]*\.?\d*", str(raw).replace(",", ""))
        price = float(m.group()) if m else None
    link = f"https://shopping.google.com/product/{result['productId']}" if result.get("productId") else (result.get("link") or "")
    return Listing(source="serper_shopping", url=link, title=result.get("title") or "",
                   fetched_at=datetime.now(tz=UTC), brand=None, mpn=None, attrs={},
                   condition="unknown", is_kit=False, kit_module_count=1,
                   unit_price_usd=price if price is not None else 0.0, kit_price_usd=None,
                   quantity_available=None, seller_name=result.get("source") or "",
                   seller_rating_pct=None, seller_feedback_count=None, ship_from_country=None)


def build_request(slug: str) -> tuple[str, str, int]:
    """Rebuild the EXACT ai_filter prompt (system + payload) for a slug, in-process,
    from the committed fixture — by intercepting ai_filter's call_llm and capturing
    the first batch's arguments. Returns (system, user_json, n)."""
    os.environ["PRODUCT_SEARCH_PRODUCTS_DIR"] = str(PROFILES_DIR)
    os.environ.setdefault("PRODUCT_SEARCH_REPORTS_DIR", str(WORK / "reports"))
    import product_search.validators.ai_filter as af
    data = json.loads((FIXTURES / SLUG_FIXTURE[slug]).read_text())
    listings = [adapt(r) for r in data.get("shopping", [])]
    profile = load_profile(slug)
    captured: dict[str, Any] = {}

    class _Stop(Exception):
        pass

    def _cap(*, provider, model, system, messages, response_format="text", max_tokens=2048):
        captured["system"] = system
        captured["user"] = messages[0].content
        raise _Stop()

    af.call_llm = _cap  # type: ignore[assignment]
    try:
        af.ai_filter(listings, profile)
    except _Stop:
        pass
    payload = json.loads(captured["user"])
    return captured["system"], json.dumps(payload, indent=2), len(payload)


def call_model(spec: dict[str, Any], system: str, user: str, temperature: float, max_tokens: int = 16384):
    """Return (text, in_tok, out_tok). Temperature set explicitly (prod ai_filter does not set it)."""
    if spec["kind"] == "anthropic":
        import anthropic
        c = anthropic.Anthropic(api_key=os.environ[spec["key_env"]])
        r = c.messages.create(model=spec["model"], system=system, max_tokens=max_tokens,
                              temperature=temperature, messages=[{"role": "user", "content": user}])
        text = next((b.text for b in r.content if hasattr(b, "text")), "")
        return text, (r.usage.input_tokens if r.usage else 0), (r.usage.output_tokens if r.usage else 0)
    if spec["kind"] == "gemini":
        import google.generativeai as genai  # type: ignore
        genai.configure(api_key=os.environ[spec["key_env"]])  # type: ignore[attr-defined]
        gm = genai.GenerativeModel(  # type: ignore[attr-defined]
            model_name=spec["model"], system_instruction=system,
            generation_config={"max_output_tokens": max_tokens, "temperature": temperature,
                               "response_mime_type": "application/json"})
        r = gm.generate_content(user)
        u = getattr(r, "usage_metadata", None)
        return (getattr(r, "text", "") or ""), getattr(u, "prompt_token_count", 0), getattr(u, "candidates_token_count", 0)
    import openai
    c = openai.OpenAI(api_key=os.environ[spec["key_env"]], base_url=spec.get("base"))
    r = c.chat.completions.create(model=spec["model"], max_tokens=max_tokens, temperature=temperature,
                                  response_format={"type": "json_object"},
                                  messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
    msg = r.choices[0].message
    text = msg.content or getattr(msg, "reasoning_content", "") or ""
    u = r.usage
    return text, (u.prompt_tokens if u else 0), (u.completion_tokens if u else 0)


def reject_set(text: str, n: int) -> set[int] | None:
    parsed = _extract_json((text or "").strip().removeprefix("```json").removeprefix("```").removesuffix("```"))
    evals = parsed.get("evaluations") if isinstance(parsed, dict) else (parsed if isinstance(parsed, list) else None)
    if not isinstance(evals, list):
        return None
    rej, seen = set(), set()
    for e in evals:
        if isinstance(e, dict) and isinstance(e.get("index"), int):
            seen.add(e["index"])
            if not e.get("pass"):
                rej.add(e["index"])
    return rej if len(seen) >= n * 0.8 else None


def jaccard(sets: list[set[int]]) -> float:
    pairs = list(itertools.combinations(sets, 2))
    if not pairs:
        return 1.0
    return sum((len(a & b) / len(a | b)) if (a | b) else 1.0 for a, b in pairs) / len(pairs)


def run_one(slug: str, trials: int, temperature: float, show_rejects: bool, payload_titles: list[str]) -> dict[str, dict]:
    system, user, n = build_request(slug)
    gold_pass = GOLD_PASS.get(slug, set(range(n)))
    print(f"\n=== {slug}  n={n}  trials={trials}  temp={temperature}  gold_pass={len(gold_pass)} ===")
    print(f"{'model':<15} {'det':>5} {'P':>5} {'R':>5} {'F1':>5} {'~$/run':>9}  counts / notes")
    print("-" * 92)
    results: dict[str, dict] = {}
    for spec in MODELS:
        if not os.environ.get(spec["key_env"]):
            continue
        rej_sets, counts, costs, notes = [], [], [], ""
        for _ in range(trials):
            try:
                text, itok, otok = call_model(spec, system, user, temperature)
            except Exception as e:
                notes = f"ERR {type(e).__name__}: {str(e)[:55]}"
                break
            rs = reject_set(text, n)
            if rs is None:
                notes = "unparseable/partial"
                continue
            rej_sets.append(rs)
            counts.append(n - len(rs))
            c = _price(spec["model"], itok, otok)
            if c is not None:
                costs.append(c)
        if not rej_sets:
            print(f"{spec['name']:<15} {'—':>5}  {notes}")
            results[spec["name"]] = {"error": notes}
            continue
        det = jaccard(rej_sets)
        last_pass = set(range(n)) - rej_sets[-1]
        tp = len(last_pass & gold_pass); fp = len(last_pass - gold_pass); fn = len(gold_pass - last_pass)
        P = tp / (tp + fp) if tp + fp else 0.0
        R = tp / (tp + fn) if tp + fn else 0.0
        F1 = 2 * P * R / (P + R) if P + R else 0.0
        cost = f"${sum(costs)/len(costs):.4f}" if costs else "n/a"
        print(f"{spec['name']:<15} {det:>5.2f} {P:>5.2f} {R:>5.2f} {F1:>5.2f} {cost:>9}  counts={counts} {notes}")
        results[spec["name"]] = {"det": det, "P": P, "R": R, "F1": F1, "cost": cost, "counts": counts}
        if show_rejects:
            for i in sorted(rej_sets[-1]):
                print(f"      reject #{i}: {payload_titles[i][:78] if i < len(payload_titles) else ''}")
    return results


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", default="ddr5-rdimm-256gb")
    ap.add_argument("--all", action="store_true", help="Run every slug x every keyed model.")
    ap.add_argument("--trials", type=int, default=3)
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--show-rejects", action="store_true")
    args = ap.parse_args()

    slugs = list(SLUG_FIXTURE) if args.all else [args.slug]
    matrix: dict[str, dict[str, dict]] = {}
    for slug in slugs:
        data = json.loads((FIXTURES / SLUG_FIXTURE[slug]).read_text())
        titles = [r.get("title", "") for r in data.get("shopping", [])]
        matrix[slug] = run_one(slug, args.trials, args.temperature, args.show_rejects, titles)

    if args.all:
        print(f"\n\n================= F1 MATRIX (model x product, temp={args.temperature}) =================")
        models_seen = [m["name"] for m in MODELS if any(m["name"] in matrix[s] for s in slugs)]
        print(f"{'model':<15}" + "".join(f"{s.split('-')[0][:10]:>11}" for s in slugs))
        for mname in models_seen:
            cells = []
            for s in slugs:
                r = matrix[s].get(mname, {})
                cells.append(f"{r['F1']:.2f}" if "F1" in r else "err")
            row = "".join(f"{c:>11}" for c in cells)
            print(f"{mname:<15}{row}")
        print("\n(also compare determinism + $/run per-product above; F1 gold is unambiguous for")
        print(" ddr5 + netanyahus, and a strict target-SKU reading for dji + the-week.)")


if __name__ == "__main__":
    main()
