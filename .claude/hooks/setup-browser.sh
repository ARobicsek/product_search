#!/bin/bash
# SessionStart hook: make a headless Chrome available for the chrome-devtools MCP.
#
# Claude Code on the web runs in a fresh, ephemeral container each session, so the
# browser has to be (re)installed at session start. The install is cached by the
# environment after the first run, so subsequent sessions are fast.
#
# Used by the live naive-user verification task (docs/NEXT_SESSION_LIVE_VERIFY.md):
# the chrome-devtools MCP drives the prod app UI; the app does its own server-side
# scraping, so plain headless Chrome is sufficient here.
set -e

# Already have a Chrome? Nothing to do.
if command -v google-chrome >/dev/null 2>&1 || command -v google-chrome-stable >/dev/null 2>&1 || command -v chromium >/dev/null 2>&1; then
  echo "setup-browser: Chrome/Chromium already present."
  exit 0
fi

echo "setup-browser: installing headless Chrome via Playwright..."
# Playwright ships a known-good Chromium build and pulls in the OS deps. This is
# more reliable in a sandboxed container than apt-getting google-chrome-stable.
if ! npx --yes playwright@latest install --with-deps chromium; then
  echo "setup-browser: 'install --with-deps' failed; retrying without --with-deps." >&2
  npx --yes playwright@latest install chromium || {
    echo "setup-browser: WARNING — could not install Chromium. chrome-devtools MCP will not connect." >&2
    exit 0   # non-fatal: don't block the session, just warn.
  }
fi
echo "setup-browser: Playwright Chromium installed."

# .mcp.json points chrome-devtools-mcp at a STABLE path (--executablePath), but
# Playwright installs into a version-pinned dir (…/chromium-<rev>/…). Resolve the
# real binary now and (re)publish a stable symlink so the static .mcp.json arg
# keeps working across Chromium version bumps. The MCP launches Chrome lazily
# (first navigate), so this finishes well before the browser is needed.
CHROME_BIN="$(find /opt/pw-browsers ${HOME}/.cache/ms-playwright -type f -name chrome -path '*chrome-linux*' 2>/dev/null | sort -V | tail -1)"
if [ -n "$CHROME_BIN" ]; then
  ln -sf "$CHROME_BIN" /usr/local/bin/chrome-for-cdmcp
  echo "setup-browser: linked /usr/local/bin/chrome-for-cdmcp -> $CHROME_BIN"
else
  echo "setup-browser: WARNING — could not locate the installed Chrome binary to symlink." >&2
fi

exit 0
