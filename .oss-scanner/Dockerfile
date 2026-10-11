# turbohtml built from source the way its tox environments build it (tox.toml), with the C extension compiled twice:
# a production-equivalent debug build in /opt/venv and the ASan/UBSan fuzz build of `tox -e fuzz-smoke` in
# /opt/venv-asan. The libFuzzer harnesses that .clusterfuzzlite/build.sh ships land in /opt/fuzzers.
FROM python:3.14-bookworm
ENV DEBIAN_FRONTEND=noninteractive \
    CC=clang \
    CXX=clang++ \
    PYTHONHASHSEED=0
# libclang-rt-14-dev carries the ASan, UBSan and libFuzzer runtimes the fuzz build and harnesses link against.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates build-essential clang libclang-rt-14-dev git \
    && rm -rf /var/lib/apt/lists/*

# The sanitizer oracles confirm a mutation with parse5, and the round-trip oracles use acorn; both need Node 20+.
COPY --from=node:22-bookworm /usr/local/bin/node /usr/local/bin/node
COPY --from=node:22-bookworm /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm

# scanner contract: the checkout lives inside the image, at /src
COPY . /src
WORKDIR /src

# Test corpora are submodules, fetched now because the scan has no network: html5lib-tests and unicodetools for the
# test suite, the rest for the sanitizer oracles. WPT is too large to clone, so fuzz.py's own sparse checkout runs here.
RUN git submodule update --init --depth 1 tests/html5lib-tests tests/conformance/unicodetools \
      tools/html5lib-python tests/conformance/DOMPurify tools/fuzz-data/h5sc tools/fuzz-data/google-fuzzing \
    && PYTHONPATH=tools python -c 'from fuzz.fuzz import _checkout_sparse_wpt; _checkout_sparse_wpt()' \
    && npm ci --ignore-scripts --prefix tools/bench/node

# Editable installs, so an edit to the C sources rebuilds on the next import. NDEBUG matches the shipped wheels, so a
# fault here is one the release build has too; meson-python finds meson and ninja on PATH.
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
RUN pip install --group test \
    && pip install --no-deps --no-build-isolation --editable . \
      --config-settings=build-dir=/src/build/debug \
      --config-settings=setup-args=-Dbuildtype=debugoptimized \
      --config-settings=setup-args=-Db_ndebug=true \
      "--config-settings=setup-args=-Dc_args=-g -fno-omit-frame-pointer"

# Same flags as tox -e fuzz-smoke: -Dfuzzing=true compiles the fuzz-only checks, and the operation limit caps XPath and
# XSLT work. dharma, six and webencodings are what tox -e fuzz-oracle adds for the sanitizer oracles.
RUN python -m venv /opt/venv-asan \
    && /opt/venv-asan/bin/pip install --group test dharma six webencodings \
    && PATH=/opt/venv-asan/bin:$PATH /opt/venv-asan/bin/pip install --no-deps --no-build-isolation --editable . \
      --config-settings=build-dir=/src/build/asan \
      --config-settings=setup-args=-Dbuildtype=debugoptimized \
      --config-settings=setup-args=-Dfuzzing=true \
      "--config-settings=setup-args=-Dc_args=-g -fno-omit-frame-pointer -fsanitize=address,undefined -DTH_OPERATION_LIMIT=100000" \
      --config-settings=setup-args=-Dc_link_args=-fsanitize=address,undefined

# The coverage-guided harnesses for the cores that link no interpreter, as .clusterfuzzlite/build.sh compiles them.
RUN mkdir -p /opt/fuzzers \
    && fuzzer() { name=$1 && shift && clang -g -O1 -fno-omit-frame-pointer -fsanitize=fuzzer,address,undefined \
      -DFUZZING_BUILD_MODE_UNSAFE_FOR_PRODUCTION -I src/turbohtml/_c "$@" -lm -o "/opt/fuzzers/${name}_fuzzer"; } \
    && fuzzer idna -DTH_IDNA_STANDALONE -DTH_IDNA_FUZZ tools/fuzz/idna_harness.c \
    && fuzzer phone -DTH_PHONE_STANDALONE -DTH_PHONE_FUZZ tools/fuzz/phone_harness.c \
    && fuzzer css_minify -DCSS_MINIFY_STANDALONE -DCSS_MINIFY_FUZZ tools/fuzz/css_harness.c \
      src/turbohtml/_c/css/minify/css.c \
    && fuzzer js_minify -DJM_STANDALONE -DJM_FUZZ tools/js_minify_harness.c src/turbohtml/_c/js/lexer.c \
      src/turbohtml/_c/js/ast.c src/turbohtml/_c/js/parser.c src/turbohtml/_c/js/printer.c src/turbohtml/_c/js/fold.c \
      src/turbohtml/_c/js/mangle.c src/turbohtml/_c/js/minify.c

# Run the test suite the way tox does and the per-PR fuzz gate, so you know the image works, but do not let a failure
# abort the build.
RUN (python -m pytest tests -q -p no:cacheprovider --ignore=tests/conformance \
      --ignore=tests/test_fuzz_self_test.py || echo "WARNING: pytest reported failures") \
    && (/opt/venv-asan/bin/python tools/fuzz/fuzz.py --mode smoke || echo "WARNING: fuzz smoke reported failures")
