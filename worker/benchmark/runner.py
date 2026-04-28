"""Benchmark runner — for each (provider, model), run the synthesizer
against every fixture, score against the bar criteria, and emit a
markdown summary at ``worker/benchmark/results/<date>.md``.

Run with:  ``python -m benchmark.runner``

Or scope to a subset:
  ``python -m benchmark.runner --providers anthropic,openai``
  ``python -m benchmark.runner --fixture 01_small_no_diff``
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

import yaml

from benchmark.criteria import CriterionResult, run_all
from product_search.llm import LLMError, Message, ProviderName, call_llm
from product_search.synthesizer import render_prompt

BENCH_DIR = Path(__file__).parent
FIXTURES_DIR = BENCH_DIR / "fixtures"
RESULTS_DIR = BENCH_DIR / "results"
PRICING_PATH = BENCH_DIR / "pricing.yaml"

# Default model slate. Update from LLM_STRATEGY.md if vendor pricing or
# model IDs move. Format: (provider, model).
DEFAULT_MODELS: list[tuple[str, str]] = [
    ("anthropic", "claude-haiku-4-5-20251001"),
    ("openai", "gpt-4o-mini"),
    ("gemini", "gemini-2.0-flash"),
    ("glm", "glm-4.5-flash"),
    ("glm", "glm-4.6"),
    ("glm", "glm-5.1"),
]


# ---------------------------------------------------------------------------
# Result records
# ---------------------------------------------------------------------------


@dataclass
class FixtureResult:
    fixture: str
    criteria: list[CriterionResult]
    input_tokens: int | None
    output_tokens: int | None
    latency_s: float
    cost_usd: float | None
    error: str | None
    report_excerpt: str

    @property
    def passed(self) -> bool:
        return self.error is None and all(c.passed for c in self.criteria)


@dataclass
class ModelResult:
    provider: str
    model: str
    fixtures: list[FixtureResult]

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}"

    @property
    def fabrication_pass_rate(self) -> float:
        """Criterion 1 — must be 100% to qualify."""
        n = len(self.fixtures)
        if n == 0:
            return 0.0
        passed = sum(
            1
            for fr in self.fixtures
            if fr.error is None
            and any(c.name == "no_fabrication" and c.passed for c in fr.criteria)
        )
        return passed / n

    @property
    def overall_pass_rate(self) -> float:
        """Fraction of fixtures where every criterion passed."""
        n = len(self.fixtures)
        if n == 0:
            return 0.0
        return sum(1 for fr in self.fixtures if fr.passed) / n

    @property
    def passes_bar(self) -> bool:
        """Per LLM_STRATEGY.md: 100% on (1) and >=9/10 on the rest."""
        n = len(self.fixtures)
        if n < 10 or self.fabrication_pass_rate < 1.0:
            return False
        return sum(1 for fr in self.fixtures if fr.passed) >= 9

    @property
    def total_cost_usd(self) -> float | None:
        costs = [fr.cost_usd for fr in self.fixtures if fr.cost_usd is not None]
        return sum(costs) if costs else None

    @property
    def avg_cost_usd(self) -> float | None:
        costs = [fr.cost_usd for fr in self.fixtures if fr.cost_usd is not None]
        return sum(costs) / len(costs) if costs else None

    @property
    def latency_p50(self) -> float:
        lats = [fr.latency_s for fr in self.fixtures if fr.error is None]
        return statistics.median(lats) if lats else float("nan")

    @property
    def latency_p95(self) -> float:
        lats = sorted(fr.latency_s for fr in self.fixtures if fr.error is None)
        if not lats:
            return float("nan")
        idx = max(0, int(round(0.95 * (len(lats) - 1))))
        return lats[idx]


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------


def _load_pricing() -> dict[str, dict[str, float]]:
    if not PRICING_PATH.exists():
        return {}
    raw = yaml.safe_load(PRICING_PATH.read_text(encoding="utf-8"))
    return cast(dict[str, dict[str, float]], (raw or {}).get("models") or {})


def _cost_usd(
    provider: str,
    model: str,
    input_tokens: int | None,
    output_tokens: int | None,
    pricing: dict[str, dict[str, float]],
) -> float | None:
    key = f"{provider}:{model}"
    rate = pricing.get(key)
    if rate is None or input_tokens is None or output_tokens is None:
        return None
    return (
        input_tokens / 1_000_000 * float(rate.get("input_per_million", 0.0))
        + output_tokens / 1_000_000 * float(rate.get("output_per_million", 0.0))
    )


# ---------------------------------------------------------------------------
# One run
# ---------------------------------------------------------------------------


def _load_fixture(name: str) -> dict[str, Any]:
    path = FIXTURES_DIR / f"{name}.json"
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def _run_one(
    provider: str,
    model: str,
    fixture_name: str,
    payload: dict[str, Any],
    system_prompt: str,
    pricing: dict[str, dict[str, float]],
) -> FixtureResult:
    user_content = json.dumps(payload, default=str, indent=2)
    started = time.perf_counter()
    error: str | None = None
    report_md = ""
    in_toks: int | None = None
    out_toks: int | None = None
    try:
        resp = call_llm(
            provider=cast(ProviderName, provider),
            model=model,
            system=system_prompt,
            messages=[Message(role="user", content=user_content)],
            max_tokens=2048,
        )
        report_md = resp.text.strip()
        in_toks = resp.input_tokens
        out_toks = resp.output_tokens
    except (LLMError, ImportError, Exception) as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
    latency = time.perf_counter() - started

    if error is not None:
        criteria = [CriterionResult("call", False, error)]
    else:
        criteria = run_all(report_md, payload)

    cost = _cost_usd(provider, model, in_toks, out_toks, pricing)
    excerpt = report_md[:600] + ("…" if len(report_md) > 600 else "")
    return FixtureResult(
        fixture=fixture_name,
        criteria=criteria,
        input_tokens=in_toks,
        output_tokens=out_toks,
        latency_s=latency,
        cost_usd=cost,
        error=error,
        report_excerpt=excerpt,
    )


def run_benchmark(
    models: list[tuple[str, str]] | None = None,
    fixture_names: list[str] | None = None,
) -> list[ModelResult]:
    models = models or DEFAULT_MODELS
    if fixture_names is None:
        fixture_names = sorted(p.stem for p in FIXTURES_DIR.glob("*.json"))

    pricing = _load_pricing()
    system_prompt = render_prompt()
    results: list[ModelResult] = []

    for provider, model in models:
        print(f"\n=== {provider}:{model} ===", file=sys.stderr)
        per_fixture: list[FixtureResult] = []
        for name in fixture_names:
            payload = _load_fixture(name)
            print(f"  [{name}] ...", end="", file=sys.stderr, flush=True)
            fr = _run_one(provider, model, name, payload, system_prompt, pricing)
            tag = "PASS" if fr.passed else ("ERR" if fr.error else "FAIL")
            cost = f"${fr.cost_usd:.5f}" if fr.cost_usd is not None else "n/a"
            print(f" {tag}  {fr.latency_s:5.2f}s  {cost}", file=sys.stderr)
            if fr.error:
                print(f"      error: {fr.error}", file=sys.stderr)
            elif not fr.passed:
                fails = [c for c in fr.criteria if not c.passed]
                for c in fails:
                    print(f"      - {c.name}: {c.detail}", file=sys.stderr)
            per_fixture.append(fr)
        results.append(ModelResult(provider, model, per_fixture))

    return results


# ---------------------------------------------------------------------------
# Markdown summary
# ---------------------------------------------------------------------------


def write_summary(
    results: list[ModelResult],
    out_path: Path,
) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append(f"# Synthesizer benchmark — {date.today().isoformat()}")
    lines.append("")
    lines.append(
        "Bar (per [LLM_STRATEGY.md](../../docs/LLM_STRATEGY.md)): 100% on "
        "fabrication, >=9/10 fixtures pass all six criteria."
    )
    lines.append("")
    lines.append(
        "| Provider:Model | Bar | Overall | Fab | Avg cost | p50 | p95 |"
    )
    lines.append(
        "|---|---|---|---|---|---|---|"
    )
    # Sort: bar-passing first, then by avg cost ascending (None last).
    def _sort_key(mr: ModelResult) -> tuple[int, float]:
        cost = mr.avg_cost_usd if mr.avg_cost_usd is not None else float("inf")
        return (0 if mr.passes_bar else 1, cost)

    for mr in sorted(results, key=_sort_key):
        bar = "PASS" if mr.passes_bar else "fail"
        avg_cost = (
            f"${mr.avg_cost_usd:.5f}" if mr.avg_cost_usd is not None else "n/a"
        )
        lines.append(
            f"| `{mr.label}` | **{bar}** | "
            f"{mr.overall_pass_rate * 100:.0f}% "
            f"({sum(1 for fr in mr.fixtures if fr.passed)}/{len(mr.fixtures)}) | "
            f"{mr.fabrication_pass_rate * 100:.0f}% | {avg_cost} | "
            f"{mr.latency_p50:.2f}s | {mr.latency_p95:.2f}s |"
        )

    lines.append("")
    lines.append("## Per-fixture details")
    lines.append("")
    for mr in results:
        lines.append(f"### `{mr.label}`")
        lines.append("")
        lines.append(
            "| Fixture | Result | Notes |"
        )
        lines.append("|---|---|---|")
        for fr in mr.fixtures:
            if fr.error:
                lines.append(f"| {fr.fixture} | ERROR | {fr.error} |")
            else:
                fails = [c for c in fr.criteria if not c.passed]
                if not fails:
                    lines.append(f"| {fr.fixture} | PASS | — |")
                else:
                    note = "; ".join(f"{c.name}: {c.detail}" for c in fails)
                    lines.append(f"| {fr.fixture} | FAIL | {note} |")
        lines.append("")

    lines.append("## Sample report excerpt")
    lines.append("")
    # Pick the cheapest passing model (or the first if none pass).
    cheapest = min(
        results, key=lambda mr: (
            (0 if mr.passes_bar else 1),
            mr.avg_cost_usd if mr.avg_cost_usd is not None else float("inf"),
        ),
        default=None,
    )
    if cheapest and cheapest.fixtures:
        first = cheapest.fixtures[0]
        lines.append(f"From `{cheapest.label}` on `{first.fixture}`:")
        lines.append("")
        lines.append("```markdown")
        lines.append(first.report_excerpt)
        lines.append("```")
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="benchmark", description="Synthesizer benchmark")
    p.add_argument(
        "--providers",
        default="anthropic,openai,gemini,glm",
        help="Comma-separated provider list. Default: all four.",
    )
    p.add_argument(
        "--fixture",
        action="append",
        default=None,
        help="Limit to a fixture name (repeatable). Default: all fixtures.",
    )
    p.add_argument(
        "--out",
        default=None,
        help="Override results path (default: results/<today>.md).",
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    selected_providers = {p.strip() for p in args.providers.split(",") if p.strip()}
    models = [(p, m) for (p, m) in DEFAULT_MODELS if p in selected_providers]
    if not models:
        print("ERROR: no providers selected.", file=sys.stderr)
        sys.exit(2)

    results = run_benchmark(models=models, fixture_names=args.fixture)

    if args.out:
        out_path = Path(args.out)
    else:
        out_path = RESULTS_DIR / f"{datetime.now(tz=UTC).date().isoformat()}.md"

    write_summary(results, out_path)
    print(f"\nWrote summary to {out_path}", file=sys.stderr)

    # Exit non-zero if no model passed the bar — useful for CI.
    if not any(mr.passes_bar for mr in results):
        print("WARNING: no model passed the bar.", file=sys.stderr)
        # Still exit 0; the goal is to produce data, not gate CI.


if __name__ == "__main__":
    main()
