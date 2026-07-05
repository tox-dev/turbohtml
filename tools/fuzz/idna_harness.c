/* Standalone ASan/UBSan/libFuzzer harness for the IDNA domain-to-ASCII engine (src/turbohtml/_c/url/idna.c).

   The research spike (tox-dev/turbohtml#478) flags punycode/UTS-46 as the highest-risk C surface: the RFC 3492
   accumulator (`i += digit * w`) is the Libidn2 CVE-2017-14062 integer-overflow class, and the output bound is the
   OpenSSL CVE-2022-3602 off-by-one class. idna.c's ToASCII core is pure Py_UCS4 buffer arithmetic, so compiling it with
   TH_IDNA_STANDALONE drops its two CPython boundary functions and lets this driver push arbitrary Unicode through the
   accumulator and the output bound under the sanitizers with no interpreter -- the same jstypes.h / JM_STANDALONE
   decoupling tools/js_minify_harness.c uses.

   The driver reimplements the th_url_to_ascii orchestration (map_host -> NFC -> per-label punycode) over the same
   buffer sizing idna.c allocates, so any miss in that sizing is an ASan out-of-bounds write here, not a silent
   corruption in the extension. Input bytes are UTF-8 decoded to code points (a lone byte that is not valid UTF-8 falls
   back to its Latin-1 value), so multi-byte and astral seeds exercise the mapping, combining-class, and Hangul rows.

   Build (macOS, ASan+UBSan; LSan is unavailable on Apple clang):
     clang -DTH_IDNA_STANDALONE -fsanitize=address,undefined -g -O1 -fno-omit-frame-pointer \
       -I src/turbohtml/_c tools/fuzz/idna_harness.c -o /tmp/idnafuzz
   Coverage-guided (libFuzzer): add -DTH_IDNA_FUZZ -fsanitize=fuzzer.

   Usage: idnafuzz [file ...]  -- runs the built-in edge cases, then ToASCII on each file's bytes. */

#ifndef TH_IDNA_STANDALONE
#define TH_IDNA_STANDALONE
#endif

#include "url/idna.c"

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Decode one UTF-8 sequence at bytes[pos]; store the code point and return its byte width. An ill-formed sequence
   decodes as the single Latin-1 byte (width 1), so every input is accepted and every byte reaches the engine. */
static size_t utf8_next(const unsigned char *bytes, size_t len, size_t pos, Py_UCS4 *cp) {
    unsigned char lead = bytes[pos];
    if (lead < 0x80) {
        *cp = lead;
        return 1;
    }
    int extra = (lead >= 0xF0) ? 3 : (lead >= 0xE0) ? 2 : (lead >= 0xC0) ? 1 : -1;
    if (extra < 0 || pos + (size_t)extra >= len) {
        *cp = lead;
        return 1;
    }
    Py_UCS4 value = lead & (0x7F >> (extra + 1));
    for (int step = 1; step <= extra; step++) {
        unsigned char cont = bytes[pos + (size_t)step];
        if ((cont & 0xC0) != 0x80) {
            *cp = lead;
            return 1;
        }
        value = (value << 6) | (cont & 0x3F);
    }
    *cp = value;
    return (size_t)extra + 1;
}

/* Run the WHATWG domain-to-ASCII pipeline over one host, mirroring th_url_to_ascii's buffer sizing so ASan bounds the
   real allocation. Frees every buffer; a label punycode cannot encode returns a negative offset and is ignored. */
static void to_ascii(const Py_UCS4 *input, Py_ssize_t in_len) {
    Py_UCS4 *mapped = malloc((size_t)(in_len * 18 + 1) * sizeof(Py_UCS4));
    if (mapped == NULL) {
        return;
    }
    Py_ssize_t mapped_len = map_host(input, in_len, mapped);
    Py_UCS4 *norm = malloc((size_t)(mapped_len * 4 + 1) * sizeof(Py_UCS4));
    if (norm == NULL) {
        free(mapped);
        return;
    }
    Py_ssize_t norm_len = nfc(mapped, mapped_len, norm);
    Py_UCS4 *out = malloc((size_t)(norm_len * 16 + 64) * sizeof(Py_UCS4));
    Py_UCS4 *scratch = malloc((size_t)(norm_len * 16 + 64) * sizeof(Py_UCS4));
    if (out == NULL || scratch == NULL) {
        free(mapped);
        free(norm);
        free(out);
        free(scratch);
        return;
    }
    Py_ssize_t at = 0;
    Py_ssize_t label_start = 0;
    for (Py_ssize_t index = 0; index <= norm_len; index++) {
        if (index < norm_len && norm[index] != '.') {
            continue;
        }
        if (label_start > 0) {
            out[at++] = '.';
        }
        Py_ssize_t next = emit_label(out, at, norm + label_start, index - label_start, scratch);
        if (next < 0) {
            break;
        }
        at = next;
        label_start = index + 1;
    }
    free(mapped);
    free(norm);
    free(out);
    free(scratch);
}

static void run_bytes(const unsigned char *bytes, size_t len) {
    Py_UCS4 *wide = malloc((len ? len : 1) * sizeof(Py_UCS4));
    if (wide == NULL) {
        return;
    }
    Py_ssize_t count = 0;
    for (size_t pos = 0; pos < len;) {
        Py_UCS4 cp = 0;
        pos += utf8_next(bytes, len, pos, &cp);
        wide[count++] = cp;
    }
    to_ascii(wide, count);
    free(wide);
}

/* Inputs that stress the punycode encode path, the xn-- decode path (the OpenSSL/Libidn2 CVE surface), the
   no-non-ASCII equivalence label, long labels, empty labels, and the mapping/drop rows -- independent of any corpus. */
static void run_builtins(void) {
    static const char *const hosts[] = {
        "",         ".",         "..",          "a.b.c",         "xn--",          "xn---",
        "xn--a",    "xn--a-",    "xn----",      "xn--nxasmq6b",  "xn--80ak6aa92e", "xn--example-.org",
        "xn--zca",  "xn--0.com", "EXAMPLE.COM", "faß.de",        "\xe2\x80\x8b" /* ZWSP */,
        "a\xcc\x81.com" /* combining acute */,  "\xe1\x84\x80\xe1\x85\xa1" /* Hangul jamo */,
        "\xf0\x9f\x98\x80.com" /* astral */,    "xn--xn--xn--",  "xn--ls8h" /* pile of poo */,
    };
    for (size_t index = 0; index < sizeof(hosts) / sizeof(hosts[0]); index++) {
        run_bytes((const unsigned char *)hosts[index], strlen(hosts[index]));
    }
    /* a label longer than the 63-octet DNS bound, to stress the encode/decode length math */
    char long_label[400];
    memset(long_label, 'a', sizeof(long_label));
    long_label[0] = 'x';
    long_label[1] = 'n';
    long_label[2] = '-';
    long_label[3] = '-';
    run_bytes((const unsigned char *)long_label, sizeof(long_label));
}

static void run_file(const char *path) {
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
        fclose(handle);
        return;
    }
    size_t got = fread(buf, 1, size > 0 ? (size_t)size : 0, handle);
    fclose(handle);
    run_bytes(buf, got);
    free(buf);
}

#ifdef TH_IDNA_FUZZ
int LLVMFuzzerTestOneInput(const unsigned char *data, size_t size) {
    run_bytes(data, size);
    return 0;
}
#else
int main(int argc, char **argv) {
    run_builtins();
    for (int index = 1; index < argc; index++) {
        run_file(argv[index]);
    }
    printf("idna harness: %d files over the ToASCII engine -- no sanitizer abort\n", argc - 1);
    return 0;
}
#endif
