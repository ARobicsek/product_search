"""Throwaway: run the real ai_filter live N times for a given provider/model,
report pass-count + the set of REJECTED indices each trial (to characterize
determinism + compare Haiku vs GLM). Spike-only; not wired into anything.
"""
import json
import os
import sys
from pathlib import Path

os.environ["PRODUCT_SEARCH_PRODUCTS_DIR"] = str(Path("tests/fixtures/profiles").resolve())
os.environ.setdefault("PRODUCT_SEARCH_REPORTS_DIR", "data/serper_spike/reports")

import product_search.validators.ai_filter as af  # noqa: E402
from product_search.llm import call_llm as real_call_llm  # noqa: E402
from product_search.llm.pricing import estimate_cost_usd, format_cost_usd  # noqa: E402
from product_search.profile import load_profile  # noqa: E402
from scripts.serper_filter_runtest import adapt  # noqa: E402

fixture, slug, prov, model, trials = (
    sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5])
)
data = json.loads(Path(fixture).read_text())
listings = [adapt(r, rewrite_urls=True) for r in data.get("shopping", [])]
profile = load_profile(slug)


def forced(*, provider, model, system, messages, response_format="text", max_tokens=2048):
    return real_call_llm(provider=prov, model=globals()["model_id"], system=system,
                         messages=messages, response_format=response_format, max_tokens=max_tokens)


globals()["model_id"] = model
af.call_llm = forced

print(f"\n### {prov}/{model}  fixture={Path(fixture).name}  n={len(listings)}  trials={trials}")
costs = []
for t in range(1, trials + 1):
    passed = af.ai_filter(listings, profile)
    rejected = sorted(e["index"] for e in af.LAST_RUN_LOG if not e.get("pass"))
    u = af.LAST_RUN_USAGE or {}
    c = estimate_cost_usd(u.get("provider"), u.get("model"), u.get("input_tokens"), u.get("output_tokens"))
    costs.append(c or 0.0)
    print(f"  trial {t}: PASS {len(passed)}/{len(listings)}  rejected={rejected}  "
          f"in={u.get('input_tokens')} out={u.get('output_tokens')} ~{format_cost_usd(c)}")
if costs:
    print(f"  avg cost/run ~ {format_cost_usd(sum(costs)/len(costs))}")
