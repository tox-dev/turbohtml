#!/usr/bin/env bash
set -euo pipefail

git submodule update --init --depth 1 tests/html5lib-tests

idna_commit=$(git rev-parse HEAD:tests/conformance/unicodetools)
idna_directory=tests/conformance/unicodetools/unicodetools/data/idna/dev
mkdir -p "$idna_directory"
curl --fail --location --silent --show-error --retry 3 \
  "https://raw.githubusercontent.com/unicode-org/unicodetools/$idna_commit/unicodetools/data/idna/dev/IdnaTestV2.txt" \
  --output "$idna_directory/IdnaTestV2.txt"
