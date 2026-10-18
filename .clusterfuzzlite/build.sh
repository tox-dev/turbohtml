#!/bin/bash -eu
# Builds every fuzzer for OSS-Fuzz and ClusterFuzzLite inside base-builder-python, from $SRC/turbohtml.

# Python coverage reports measure the Atheris targets; native profiling would leave the extension needing a profile
# runtime that no preloaded library provides.
if [[ $SANITIZER == coverage ]]; then
    export CFLAGS=${CFLAGS/$COVERAGE_FLAGS/}
fi
# meson's compiler sanity check links an executable, which needs the sanitizer runtime the flags name. The extension
# module itself links with --no-undefined off, since it takes the sanitizer and coverage callbacks from the runtime
# each fuzzer preloads. The OSS-Fuzz CFLAGS define FUZZING_BUILD_MODE_UNSAFE_FOR_PRODUCTION, whose branches call into
# core/fuzzing.c, which meson compiles only with -Dfuzzing=true.
export LDFLAGS=$CFLAGS
python3 -m pip install --no-deps --no-build-isolation . --config-settings=setup-args=-Db_lundef=false \
    --config-settings=setup-args=-Dfuzzing=true

# Atheris drops a callback's return value, so libFuzzer cannot tell a documented rejection from an input worth keeping.
# The preloaded sanitizer runtime is rebuilt with tools/fuzz/atheris_bridge.c interposed on LLVMFuzzerRunDriver.
fuzzer_archive=$(python3 -c 'import atheris, os; print(os.path.join(atheris.path(), "libclang_rt.fuzzer_no_main.a"))')
case $SANITIZER in
    address) sanitizer_runtime=(--sanitizer "$($CC -print-file-name=libclang_rt.asan.a)") ;;
    undefined) sanitizer_runtime=(--sanitizer "$($CC -print-file-name=libclang_rt.ubsan_standalone.a)") ;;
    *) sanitizer_runtime=() ;;
esac
export PYTHONPATH=$SRC/turbohtml/tools
python3 -m fuzz.atheris_runtime --archive "$fuzzer_archive" --output "$WORK/atheris-runtime" "${sanitizer_runtime[@]}"
# The runner treats executable files in $OUT as fuzz targets; the preloaded library is data, not a target.
install -m 0644 "$WORK/atheris-runtime/libatheris_fuzzer.so" "$OUT/sanitizer_with_fuzzer.so"

export LD_PRELOAD=$OUT/sanitizer_with_fuzzer.so ASAN_OPTIONS=detect_leaks=0
mkdir -p "$WORK/seeds"
python3 -m fuzz.oss_fuzz seeds --output "$WORK/seeds"
for harness in $(python3 -m fuzz.oss_fuzz harnesses --output "$WORK/harnesses"); do
    # the driver imports atheris by name at run time, which PyInstaller's import scan does not follow
    compile_python_fuzzer "$harness" --paths "$SRC/turbohtml/tools" --collect-all turbohtml --hidden-import atheris
    zip -jq "$OUT/$(basename "$harness" .py)_seed_corpus.zip" "$WORK/seeds/"*
done
unset LD_PRELOAD

# The native harnesses link libFuzzer directly; a Python coverage build has no use for them.
if [[ $SANITIZER != coverage ]]; then
    read -ra compile_flags <<< "$CFLAGS"
    read -ra link_flags <<< "$CXXFLAGS"
    native() {
        local name=$1 seeds=$2 && shift 2
        "$CC" "${compile_flags[@]}" -I "$SRC/turbohtml/src/turbohtml/_c" -c "$@"
        "$CXX" "${link_flags[@]}" ./*.o "$LIB_FUZZING_ENGINE" -o "$OUT/${name}_fuzzer"
        rm ./*.o
        if [[ -n $seeds ]]; then
            zip -jq "$OUT/${name}_fuzzer_seed_corpus.zip" "$seeds"/*
        fi
    }
    mkdir -p "$WORK/native" && pushd "$WORK/native" > /dev/null
    native idna "$SRC/turbohtml/tools/fuzz/corpus/idna" -DTH_IDNA_STANDALONE -DTH_IDNA_FUZZ \
        "$SRC/turbohtml/tools/fuzz/idna_harness.c"
    native phone "$SRC/turbohtml/tools/fuzz/corpus/phone" -DTH_PHONE_STANDALONE -DTH_PHONE_FUZZ \
        "$SRC/turbohtml/tools/fuzz/phone_harness.c"
    native js_minify "" -DJM_STANDALONE -DJM_FUZZ "$SRC/turbohtml/tools/js_minify_harness.c" \
        "$SRC"/turbohtml/src/turbohtml/_c/js/{lexer,ast,parser,printer,fold,mangle,minify}.c
    popd > /dev/null
fi
