/* Standalone ASan/UBSan/libFuzzer harness for the IDNA domain-to-ASCII engine (src/turbohtml/_c/url/idna.c).

   The research spike (tox-dev/turbohtml#478) flags punycode/UTS-46 as the highest-risk C surface: the RFC 3492
   accumulator (`i += digit * w`) is the Libidn2 CVE-2017-14062 integer-overflow class, and the output bound is the
   OpenSSL CVE-2022-3602 off-by-one class. idna.c's ToASCII core is pure Py_UCS4 buffer arithmetic, so compiling it with
   TH_IDNA_STANDALONE drops its two CPython boundary functions and lets this driver push arbitrary Unicode through the
   accumulator and the output bound under the sanitizers with no interpreter -- the same jstypes.h / JM_STANDALONE
   decoupling tools/js_minify_harness.c uses.

   The driver calls the shared production orchestration, including its NFC quick check. Input bytes are UTF-8
   decoded to code points (a lone byte that is not valid UTF-8 falls
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

static void require_property(int condition, const char *property) {
    if (!condition) {
        fprintf(stderr, "IDNA invariant failed: %s\n", property);
        abort();
    }
}

static idna_status mapped_nfc(const Py_UCS4 *input, Py_ssize_t len, Py_UCS4 **output, Py_ssize_t *output_len) {
    Py_UCS4 *mapped = malloc((size_t)(len * 18 + 1) * sizeof(Py_UCS4));
    if (mapped == NULL) {
        return IDNA_NO_MEMORY;
    }
    Py_ssize_t mapped_len = map_host(input, len, mapped);
    if (mapped_len < 0) {
        free(mapped);
        return IDNA_DISALLOWED;
    }
    Py_UCS4 *normalized = malloc((size_t)(mapped_len * 8 + 1) * sizeof(Py_UCS4));
    if (normalized == NULL) {
        free(mapped);
        return IDNA_NO_MEMORY;
    }
    *output_len = nfc(mapped, mapped_len, normalized);
    *output = normalized;
    free(mapped);
    return IDNA_OK;
}

static void check_normalization(const Py_UCS4 *input, Py_ssize_t len) {
    Py_UCS4 *normalized = malloc((size_t)(len * 8 + 1) * sizeof(Py_UCS4));
    if (normalized == NULL) {
        return;
    }
    Py_ssize_t normalized_len = nfc(input, len, normalized);
    if (nfc_is_normalized(input, len)) {
        require_property(normalized_len == len && memcmp(input, normalized, (size_t)len * sizeof(Py_UCS4)) == 0,
                         "NFC quick check");
    }
    free(normalized);
    Py_UCS4 *mapped;
    Py_ssize_t mapped_len;
    if (mapped_nfc(input, len, &mapped, &mapped_len) != IDNA_OK) {
        return;
    }
    Py_UCS4 *repeated;
    Py_ssize_t repeated_len;
    idna_status status = mapped_nfc(mapped, mapped_len, &repeated, &repeated_len);
    if (status != IDNA_NO_MEMORY) {
        require_property(status == IDNA_OK, "mapped NFC rejection");
        require_property(mapped_len == repeated_len &&
                             memcmp(mapped, repeated, (size_t)mapped_len * sizeof(Py_UCS4)) == 0,
                         "mapped NFC fixpoint");
        free(repeated);
    }
    free(mapped);
}

static void check_ascii(const Py_UCS4 *output, Py_ssize_t len) {
    require_property(span_is_ascii(output, len), "ASCII output");
    Py_UCS4 *decoded = malloc((size_t)(len + 1) * sizeof(Py_UCS4));
    Py_UCS4 *encoded = malloc((size_t)(len * 16 + 64) * sizeof(Py_UCS4));
    if (decoded == NULL || encoded == NULL) {
        free(decoded);
        free(encoded);
        return;
    }
    Py_ssize_t start = 0;
    for (Py_ssize_t end = 0; end <= len; end++) {
        if (end < len && output[end] != '.') {
            continue;
        }
        if (has_xn_prefix(output + start, end - start)) {
            Py_ssize_t count = puny_decode(output + start + 4, end - start - 4, decoded);
            if (count >= 0) {
                Py_ssize_t encoded_len = puny_encode(decoded, count, encoded);
                if (encoded_len != PUNY_NO_MEMORY) {
                    require_property(
                        encoded_len == end - start - 4 &&
                            memcmp(output + start + 4, encoded, (size_t)encoded_len * sizeof(Py_UCS4)) == 0,
                        "Punycode round trip");
                }
            }
        }
        start = end + 1;
    }
    free(decoded);
    free(encoded);
    if (len <= TH_IDNA_MAX_INPUT) {
        Py_UCS4 *repeated;
        Py_ssize_t repeated_len;
        idna_status status = idna_to_ascii(output, len, &repeated, &repeated_len);
        if (status != IDNA_NO_MEMORY) {
            require_property(status == IDNA_OK, "ASCII output rejection");
            require_property(repeated_len == len && memcmp(output, repeated, (size_t)len * sizeof(Py_UCS4)) == 0,
                             "ToASCII fixpoint");
            free(repeated);
        }
    }
}

/* Count the code points, then allocate that many, so ASan flags a read one past the last. A block of one slot per byte
   leaves a spare slot for each continuation byte, and ASan misses an over-read into it. */
static Py_UCS4 *widen(const unsigned char *bytes, size_t len, Py_ssize_t *count) {
    Py_UCS4 cp;
    *count = 0;
    for (size_t pos = 0; pos < len; ++*count) {
        pos += utf8_next(bytes, len, pos, &cp);
    }
    Py_UCS4 *wide = malloc((size_t)*count * sizeof(Py_UCS4));
    if (wide == NULL) {
        return NULL;
    }
    Py_ssize_t index = 0;
    for (size_t pos = 0; pos < len; index++) {
        pos += utf8_next(bytes, len, pos, &wide[index]);
    }
    return wide;
}

static void run_bytes(const unsigned char *bytes, size_t len) {
    Py_ssize_t count;
    Py_UCS4 *wide = widen(bytes, len, &count);
    if (wide == NULL) {
        return;
    }
    if (count <= TH_IDNA_MAX_INPUT) {
        check_normalization(wide, count);
        Py_UCS4 *output;
        Py_ssize_t output_len;
        if (idna_to_ascii(wide, count, &output, &output_len) == IDNA_OK) {
            check_ascii(output, output_len);
            free(output);
        }
    }
    free(wide);
}

#ifdef TH_IDNA_FUZZ
int LLVMFuzzerTestOneInput(const unsigned char *data, size_t size) {
    run_bytes(data, size);
    return 0;
}
#else
/* Inputs that stress the punycode encode path, the xn-- decode path (the OpenSSL/Libidn2 CVE surface), the
   no-non-ASCII equivalence label, long labels, empty labels, and the mapping/drop rows -- independent of any corpus. */
static void run_builtins(void) {
    static const char *const hosts[] = {
        "",
        ".",
        "..",
        "a.b.c",
        "xn--",
        "xn---",
        "xn--a",
        "xn--a-",
        "xn----",
        "xn--nxasmq6b",
        "xn--80ak6aa92e",
        "xn--example-.org",
        "xn--zca",
        "xn--0.com",
        "EXAMPLE.COM",
        "faß.de",
        "\xe2\x80\x8b" /* ZWSP */,
        "a\xcc\x81.com" /* combining acute */,
        "\xe1\x84\x80\xe1\x85\xa1" /* Hangul jamo */,
        "\xf0\x9f\x98\x80.com" /* astral */,
        "xn--xn--xn--",
        "xn--ls8h" /* pile of poo */,
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
    size_t capacity = size > 0 ? (size_t)size : 0;
    unsigned char *buf = malloc(capacity);
    if (buf == NULL) {
        fclose(handle);
        return;
    }
    size_t got = fread(buf, 1, capacity, handle);
    fclose(handle);
    run_bytes(buf, got);
    free(buf);
}

int main(int argc, char **argv) {
    run_builtins();
    for (int index = 1; index < argc; index++) {
        run_file(argv[index]);
    }
    printf("idna harness: %d files over the ToASCII engine -- no sanitizer abort\n", argc - 1);
    return 0;
}
#endif
