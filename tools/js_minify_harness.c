/* Standalone sanitizer / fuzz harness for the JavaScript-minifier engine.

   Compiled with JM_STANDALONE the engine (lexer, ast, parser, printer, minify) uses
   the system allocator and needs no CPython runtime, so this driver can push a whole
   corpus through it under AddressSanitizer + LeakSanitizer + UndefinedBehaviorSanitizer
   with a clean signal — no interpreter shutdown leaks to suppress, no allocator
   gymnastics. Every input source byte is widened to one code point (Latin-1), which
   exercises every engine path; a few astral/multi-byte literals cover the wide-char
   branches. Each input is minified, and its output minified again, so the idempotence
   path allocates too. The returned buffer is freed, so any miss is a real leak.

   Build (macOS, ASan+UBSan; LSan is unavailable on Apple clang):
     clang -DJM_STANDALONE -fsanitize=address,undefined -g -O1 -fno-omit-frame-pointer \
       -I src/turbohtml/_c tools/js_minify_harness.c \
       src/turbohtml/_c/js/{lexer,ast,parser,printer,minify}.c -o /tmp/jsmin
   Build (Linux, adds LSan automatically): same command; run ASAN_OPTIONS=detect_leaks=1.

   Usage: jsmin [file ...]   — minifies each file (and the built-in edge cases). */

#ifndef JM_STANDALONE
#define JM_STANDALONE
#endif
#include "js/minify.h"

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Widen len bytes to a freshly malloc'd Py_UCS4 buffer (Latin-1: byte -> code point). */
static Py_UCS4 *widen(const unsigned char *bytes, size_t len, Py_ssize_t *out_len) {
    Py_UCS4 *wide = malloc((len ? len : 1) * sizeof(Py_UCS4));
    if (wide == NULL) {
        perror("malloc");
        exit(2);
    }
    for (size_t index = 0; index < len; index++) {
        wide[index] = bytes[index];
    }
    *out_len = (Py_ssize_t)len;
    return wide;
}

/* Run one input through the minifier twice (source, then its own output) and free
   every buffer. A parse error is expected for unsupported constructs and leaks
   nothing. Returns 1 if the source minified, 0 if it failed to parse. */
static int run_once(const Py_UCS4 *src, Py_ssize_t len) {
    char err[160];
    Py_ssize_t out_len = 0;
    Py_UCS4 *out = th_js_minify(src, len, 1, 1, &out_len, err, sizeof(err));
    if (out == NULL) {
        return 0; /* parse error (err set) or OOM (err empty) — nothing allocated leaks */
    }
    Py_ssize_t round_len = 0;
    Py_UCS4 *round = th_js_minify(out, out_len, 1, 1, &round_len, err, sizeof(err));
    free(round); /* may be NULL; free(NULL) is a no-op */
    free(out);
    return 1;
}

static int run_bytes(const unsigned char *bytes, size_t len) {
    Py_ssize_t wlen = 0;
    Py_UCS4 *wide = widen(bytes, len, &wlen);
    int minified = run_once(wide, wlen);
    free(wide);
    return minified;
}

static void run_file(const char *path, long *files, long *minified) {
    FILE *handle = fopen(path, "rb");
    if (handle == NULL) {
        fprintf(stderr, "skip %s: %s\n", path, strerror(errno));
        return;
    }
    fseek(handle, 0, SEEK_END);
    long size = ftell(handle);
    fseek(handle, 0, SEEK_SET);
    unsigned char *buf = malloc(size > 0 ? (size_t)size : 1);
    if (buf == NULL) {
        perror("malloc");
        exit(2);
    }
    size_t got = fread(buf, 1, (size_t)size, handle);
    fclose(handle);
    *files += 1;
    *minified += run_bytes(buf, got);
    free(buf);
}

/* Inputs that stress the empty-output, deep-nesting, regex/template and adjacency
   paths regardless of any external corpus. */
static void run_builtins(long *cases) {
    static const char *const snippets[] = {
        "", " ", ";", ";;;", "//c\n", "/*x*/", "\n\n",
        "var x=1", "a=b/c/d", "x=/ab+/g.test(s)", "return/re/.exec(x)",
        "let s=`a${x+1}b${y}c`", "0xFF 0b101 1_000 42n .5 1e9",
        "obj?.prop??a**b>>>=c", "class C extends B{#p=1;get x(){}static m(){}}",
        "for(var a=(b in c);;);", "(foo?.bar()).baz=true", "a<! --b", "a-- >b",
        "new a.b.C(1)", "1 .toString()", "x=[1,,3,,]", "switch(x){case 1:a();break;default:b()}",
        "x={async*[k](){yield* a}}", "label:for(;;)break label", "(function(){})()",
    };
    for (size_t index = 0; index < sizeof(snippets) / sizeof(snippets[0]); index++) {
        const char *text = snippets[index];
        run_bytes((const unsigned char *)text, strlen(text));
        *cases += 1;
    }
    /* a non-ASCII identifier and an astral code point in a string, to widen coverage */
    static const Py_UCS4 unicode_ident[] = {'v', 'a', 'r', ' ', 0x00E9, '=', '1', 0};
    run_once(unicode_ident, 7);
    static const Py_UCS4 astral[] = {'x', '=', '"', 0x1F600, '"', 0};
    run_once(astral, 5);
    *cases += 2;
}

int main(int argc, char **argv) {
    long files = 0;
    long minified = 0;
    long builtins = 0;
    run_builtins(&builtins);
    for (int index = 1; index < argc; index++) {
        run_file(argv[index], &files, &minified);
    }
    printf("harness: %ld builtins + %ld files (%ld minified, %ld parse-failed) — no sanitizer abort\n",
           builtins, files, minified, files - minified);
    return 0;
}

#ifdef JM_FUZZ
/* libFuzzer entry point: build with -DJM_FUZZ -fsanitize=fuzzer,address,undefined. */
int LLVMFuzzerTestOneInput(const unsigned char *data, size_t size) {
    run_bytes(data, size);
    return 0;
}
#endif
