#!/bin/bash
# Installeert de Python-afhankelijkheden in Claude Code on the web-sessies.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"

# Systeembibliotheken voor WeasyPrint (PDF-opmaak), zie packages.txt
if command -v apt-get >/dev/null 2>&1; then
  missing=$(xargs -a packages.txt -n1 sh -c 'dpkg -s "$0" >/dev/null 2>&1 || echo "$0"')
  if [ -n "$missing" ]; then
    apt-get update -qq && apt-get install -y -qq --no-install-recommends $missing || \
      echo "Waarschuwing: kon systeempakketten niet installeren: $missing" >&2
  fi
fi

pip install -q --root-user-action=ignore -r requirements.txt pyflakes

echo 'export PYTHONPATH="$CLAUDE_PROJECT_DIR"' >> "$CLAUDE_ENV_FILE"
