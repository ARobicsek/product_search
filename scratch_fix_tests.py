
import re

with open(r"c:\Users\ariro\OneDrive\Personal\Product search\worker\tests\test_universal_ai.py", "r", encoding="utf-8") as f:
    content = f.read()

content = content.replace("universal_ai.LAST_RUN_USAGE = None", "universal_ai.tls.last_run_usage = None")
content = content.replace("universal_ai.LAST_RUN_USAGE", "getattr(universal_ai.tls, \"last_run_usage\", None)")
content = content.replace("getattr(universal_ai.tls, \"last_run_usage\", None)[", "universal_ai.tls.last_run_usage[")

content = content.replace("universal_ai.LAST_SKIP_REASON", "getattr(universal_ai.tls, \"last_skip_reason\", None)")
content = content.replace("universal_ai.LAST_FETCH_DIAGNOSTICS", "getattr(universal_ai.tls, \"last_fetch_diagnostics\", None)")
content = content.replace("universal_ai._LAST_ALTERLAB_POOL_EXHAUSTED", "getattr(universal_ai.tls, \"last_alterlab_pool_exhausted\", False)")

with open(r"c:\Users\ariro\OneDrive\Personal\Product search\worker\tests\test_universal_ai.py", "w", encoding="utf-8") as f:
    f.write(content)
print("Replaced")

