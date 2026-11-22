#!/usr/bin/env bash
set -euo pipefail

# github.com at times answers a public clone with an auth prompt for a few seconds, and git's own retry comes at once
for attempt in 1 2 3 4 5; do
  git submodule update --init --depth 1 tests/html5lib-tests && break
  if ((attempt == 5)); then
    exit 1
  fi
  sleep $((attempt * 15))
done

idna_commit=$(git rev-parse HEAD:tests/conformance/unicodetools)
idna_directory=tests/conformance/unicodetools/unicodetools/data/idna/dev
mkdir -p "$idna_directory"
curl --fail --location --silent --show-error --retry 3 --retry-all-errors --ssl-revoke-best-effort \
  "https://raw.githubusercontent.com/unicode-org/unicodetools/$idna_commit/unicodetools/data/idna/dev/IdnaTestV2.txt" \
  --output "$idna_directory/IdnaTestV2.txt"
