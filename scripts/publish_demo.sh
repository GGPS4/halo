#!/usr/bin/env bash
# Publish halo/web to the gh-pages branch so the demo at https://ggps4.github.io/halo/ updates.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 scripts/build_demo.py
git add halo/web/demo.js && git diff --cached --quiet || git commit -qm "Rebuild demo.js"
git subtree split --prefix halo/web -b gh-pages-tmp >/dev/null
git push -f origin gh-pages-tmp:gh-pages
git branch -D gh-pages-tmp >/dev/null
