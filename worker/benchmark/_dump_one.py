"""Tiny helper: run one (provider, model, fixture) and dump the raw report.

Use during prompt iteration. Not part of the benchmark.

Run: python -m benchmark._dump_one anthropic claude-haiku-4-5-20251001 01_small_no_diff
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import cast

from product_search.llm import Message, ProviderName, call_llm
from product_search.synthesizer import render_prompt


def main() -> None:
    if len(sys.argv) != 4:
        print("usage: provider model fixture_name", file=sys.stderr)
        sys.exit(2)
    provider, model, fixture_name = sys.argv[1], sys.argv[2], sys.argv[3]
    payload_path = Path(__file__).parent / "fixtures" / f"{fixture_name}.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    system_prompt = render_prompt()
    user_content = json.dumps(payload, default=str, indent=2)
    resp = call_llm(
        provider=cast(ProviderName, provider),
        model=model,
        system=system_prompt,
        messages=[Message(role="user", content=user_content)],
        max_tokens=2048,
    )
    print(resp.text)
    print("\n---usage---", file=sys.stderr)
    print(f"in={resp.input_tokens} out={resp.output_tokens}", file=sys.stderr)


if __name__ == "__main__":
    main()
