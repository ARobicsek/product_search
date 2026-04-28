"""Synthesizer benchmark — pick the cheapest LLM that passes the bar.

Not part of the runtime worker. Lives at ``worker/benchmark/`` rather than
inside ``worker/src/product_search/`` because it is dev tooling, not
something the daily workflow imports.
"""
