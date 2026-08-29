/* Standalone ASan/UBSan/libFuzzer harness for the phone-number recognizer (src/turbohtml/_c/clean/phone.c): with
   no interpreter, each fixed-size buffer in the recognizer is an ASan bound. An ill-formed UTF-8 byte falls back to
   its Latin-1 value, so mangled multi-byte digit scripts still reach the tables.

   Build (macOS, ASan+UBSan; LSan is unavailable on Apple clang):
     clang -fsanitize=address,undefined -g -O1 -fno-omit-frame-pointer -I src/turbohtml/_c \
       tools/fuzz/phone_harness.c -o /tmp/phonefuzz
   Coverage-guided (libFuzzer): add -DTH_PHONE_FUZZ -fsanitize=fuzzer.

   Usage: phonefuzz [file ...]                       -- built-in edge cases, then each file's bytes, per configuration
          phonefuzz --dump REGIONS MODE [OPTIONS]    -- one text per stdin line; print its matches, then `--`
   REGIONS is a comma-separated list (or `-` for none), MODE is `valid` or `possible`, OPTIONS may contain
   `separators`, `cards` (keep card numbers), `noprefix` (do not require the national prefix), `strict` or
   `exact` (the grouping leniencies) and `labels`. The dump form is what tests/clean/test_phone_model.py diffs
   against the Python model. */

#include "clean/phone.c"

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static size_t utf8_next(const unsigned char *bytes, size_t len, size_t pos, uint32_t *cp) {
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
    uint32_t value = lead & (0x7F >> (extra + 1));
    for (int step = 1; step <= extra; step++) {
        unsigned char cont = bytes[pos + (size_t)step];
        if ((cont & 0xC0) != 0x80) {
            *cp = lead;
            return 1;
        }
        value = (value << 6) | (cont & 0x3F);
    }
    *cp = value > 0x10FFFF ? lead : value;
    return (size_t)extra + 1;
}

static uint32_t read_wide(const void *text, size_t index) {
    return ((const uint32_t *)text)[index];
}

static th_phone_label default_labels[TH_PHONE_LABEL_COUNT];

static void fill_labels(th_phone_config *config) {
    for (size_t index = 0; index < TH_PHONE_LABEL_COUNT; index++) {
        default_labels[index].text = th_phone_default_labels[index];
        default_labels[index].len = (uint8_t)strlen(th_phone_default_labels[index]);
    }
    config->labels = default_labels;
    config->label_count = TH_PHONE_LABEL_COUNT;
}

static int add_region(th_phone_config *config, const char *code) {
    int index = th_phone_region_index(code, strlen(code));
    if (index < 0 || config->region_count == TH_PHONE_MAX_REGIONS) {
        return 0;
    }
    config->regions[config->region_count++] = (uint16_t)index;
    return 1;
}

/* The link scanner's digit arm, mirrored. */
static void scan(const uint32_t *wide, size_t count, const th_phone_config *config, FILE *out) {
    size_t position = 0;
    size_t left_bound = 0;
    while (position < count) {
        if (th_phone_digit_value(wide[position]) < 0) {
            position++;
            continue;
        }
        th_phone_match match;
        size_t retry = 0;
        if (th_phone_find(read_wide, wide, count, left_bound, position, config, &match, &retry)) {
            left_bound = match.end;
            if (out != NULL) {
                fprintf(out, "%zu %zu %u %.*s %s %s %u\n", match.start, match.end, match.country_code,
                        (int)match.nsn_len, match.nsn, match.ext_len ? match.ext : "-",
                        match.region < 0 ? "-" : th_phone_regions[match.region].code, match.type);
            }
        }
        position = retry > position + 1 ? retry : position + 1;
    }
}

static size_t widen(const unsigned char *bytes, size_t len, uint32_t *wide) {
    size_t count = 0;
    for (size_t pos = 0; pos < len;) {
        uint32_t cp = 0;
        pos += utf8_next(bytes, len, pos, &cp);
        wide[count++] = cp;
    }
    return count;
}

static void run_bytes(const unsigned char *bytes, size_t len) {
    uint32_t *wide = malloc((len ? len : 1) * sizeof(uint32_t));
    if (wide == NULL) {
        return;
    }
    size_t count = widen(bytes, len, wide);
    static const char *const region_sets[][3] = {
        {"US", NULL, NULL}, {"GB", "DE", NULL}, {NULL, NULL, NULL}, {"JP", "IN", "BR"}};
    for (size_t set = 0; set < sizeof(region_sets) / sizeof(region_sets[0]); set++) {
        for (int valid = 0; valid < 2; valid++) {
            th_phone_config config = {.require_valid = (uint8_t)valid,
                                      .skip_card_numbers = 1,
                                      .require_national_prefix = 1,
                                      .type_mask = 0x7FF};
            for (size_t slot = 0; slot < 3 && region_sets[set][slot] != NULL; slot++) {
                add_region(&config, region_sets[set][slot]);
            }
            config.require_separators = (uint8_t)(set & 1);
            config.grouping = (uint8_t)(valid ? set % 3 : 0);
            if (set == 3) {
                fill_labels(&config);
            }
            th_phone_config_floor(&config);
            scan(wide, count, &config, NULL);
        }
    }
    free(wide);
}

static void run_builtins(void) {
    static const char *const texts[] = {
        "",
        "1",
        "+",
        "+1",
        "650-253-0000",
        "+1 650-253-0000 x 1234",
        "((02) 1234 5678)",
        "(1 (506) 234-5678)",
        "1/2/2011 12:30:45",
        "4111 1111 1111 1111",
        "192.168.0.1",
        "order 123456789",
        "0000000000000000000000000000000000000000",
        "1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2",
        "\xef\xbc\x8b\xef\xbc\x91\xef\xbc\x96\xef\xbc\x95\xef\xbc\x90" /* fullwidth +1650 */,
        "\xd9\xa0\xd9\xa1\xd9\xa2\xd9\xa3\xd9\xa4\xd9\xa5\xd9\xa6\xd9\xa7\xd9\xa8\xd9\xa9" /* arabic-indic */,
        "+44 20 7946 0958;ext=123456789012345678901234567890",
        "tel. 030 12345678 ext. 99 x 55 # 77 ~ 88",
        "\xe2\x80\x93\xe2\x80\x93 12 \xe2\x80\x93 34 \xe2\x80\x93 56 \xe2\x80\x93 78 \xe2\x80\x93 90",
        "12345678901234567890 12345678901234567890 12345678901234567890 12345678901234567890 12345678901234567890 "
        "12345678901234567890 12345678901234567890 12345678901234567890 12345678901234567890 12345678901234567890 "
        "12345678901234567890 12345678901234567890 12345678901234567890 12345678901234567890 12345678901234567890 "
        "12345678901234567890 12345678901234567890 12345678901234567890 12345678901234567890 12345678901234567890 "
        "12345678901234567890 12345678901234567890 12345678901234567890",
    };
    for (size_t index = 0; index < sizeof(texts) / sizeof(texts[0]); index++) {
        run_bytes((const unsigned char *)texts[index], strlen(texts[index]));
    }
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

static int run_dump(int argc, char **argv) {
    if (argc < 4) {
        fprintf(stderr, "usage: --dump REGIONS MODE [OPTIONS]\n");
        return 2;
    }
    th_phone_config config = {
        .require_valid = (uint8_t)(strcmp(argv[3], "valid") == 0), .skip_card_numbers = 1, .type_mask = 0x7FF};
    char regions[64];
    snprintf(regions, sizeof(regions), "%s", argv[2]);
    for (char *code = strtok(regions, ","); code != NULL; code = strtok(NULL, ",")) {
        if (strcmp(code, "-") != 0 && !add_region(&config, code)) {
            fprintf(stderr, "unknown region %s\n", code);
            return 2;
        }
    }
    const char *options = argc > 4 ? argv[4] : "";
    config.require_separators = strstr(options, "separators") != NULL;
    config.skip_card_numbers = strstr(options, "cards") == NULL;
    config.require_national_prefix = strstr(options, "noprefix") == NULL;
    config.grouping = strstr(options, "exact") != NULL ? 2 : strstr(options, "strict") != NULL ? 1 : 0;
    if (strstr(options, "labels") != NULL) {
        fill_labels(&config);
    }
    th_phone_config_floor(&config);
    char *line = NULL;
    size_t capacity = 0;
    ssize_t got;
    while ((got = getline(&line, &capacity, stdin)) >= 0) {
        if (got > 0 && line[got - 1] == '\n') {
            got--;
        }
        uint32_t *wide = malloc((got > 0 ? (size_t)got : 1) * sizeof(uint32_t));
        if (wide == NULL) {
            return 2;
        }
        scan(wide, widen((const unsigned char *)line, (size_t)got, wide), &config, stdout);
        free(wide);
        puts("--");
    }
    free(line);
    return 0;
}

#ifdef TH_PHONE_FUZZ
int LLVMFuzzerTestOneInput(const unsigned char *data, size_t size) {
    run_bytes(data, size);
    return 0;
}
#else
int main(int argc, char **argv) {
    if (argc > 1 && strcmp(argv[1], "--dump") == 0) {
        return run_dump(argc, argv);
    }
    run_builtins();
    for (int index = 1; index < argc; index++) {
        run_file(argv[index]);
    }
    printf("phone harness: %d files over the recognizer -- no sanitizer abort\n", argc - 1);
    return 0;
}
#endif
