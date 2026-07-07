/* Built-in XSD datatypes and constraining facets, shared by the XSD engine and the
   RELAX NG <data> element when it names the XSD datatype library. Included once into
   validate/schema.c; every definition is static. Lexical spaces follow XSD 1.0 part 2;
   numeric facet bounds compare as doubles, which is exact for the value ranges a real
   schema constrains.

   whiteSpace runs first (preserve / replace / collapse), then the value is checked
   against its datatype's lexical space, then every constraining facet. */

#ifndef TURBOHTML_VALIDATE_DATATYPES_H
#define TURBOHTML_VALIDATE_DATATYPES_H

enum datatype {
    DT_STRING,
    DT_NORMALIZED_STRING,
    DT_TOKEN,
    DT_BOOLEAN,
    DT_DECIMAL,
    DT_INTEGER,
    DT_LONG,
    DT_INT,
    DT_SHORT,
    DT_BYTE,
    DT_NON_NEGATIVE_INTEGER,
    DT_POSITIVE_INTEGER,
    DT_NON_POSITIVE_INTEGER,
    DT_NEGATIVE_INTEGER,
    DT_UNSIGNED_LONG,
    DT_UNSIGNED_INT,
    DT_UNSIGNED_SHORT,
    DT_UNSIGNED_BYTE,
    DT_FLOAT,
    DT_DOUBLE,
    DT_DATE,
    DT_DATE_TIME,
    DT_TIME,
    DT_DURATION,
    DT_ANY_URI,
    DT_QNAME,
    DT_NCNAME,
    DT_NAME,
    DT_NMTOKEN,
    DT_LANGUAGE,
    DT_HEX_BINARY,
    DT_BASE64_BINARY,
    DT_ANY_SIMPLE_TYPE,
    DT_UNKNOWN,
};

typedef struct {
    const char *name;
    int id;
} dt_name_entry;

static const dt_name_entry DT_NAMES[] = {
    {"string", DT_STRING},
    {"normalizedString", DT_NORMALIZED_STRING},
    {"token", DT_TOKEN},
    {"boolean", DT_BOOLEAN},
    {"decimal", DT_DECIMAL},
    {"integer", DT_INTEGER},
    {"long", DT_LONG},
    {"int", DT_INT},
    {"short", DT_SHORT},
    {"byte", DT_BYTE},
    {"nonNegativeInteger", DT_NON_NEGATIVE_INTEGER},
    {"positiveInteger", DT_POSITIVE_INTEGER},
    {"nonPositiveInteger", DT_NON_POSITIVE_INTEGER},
    {"negativeInteger", DT_NEGATIVE_INTEGER},
    {"unsignedLong", DT_UNSIGNED_LONG},
    {"unsignedInt", DT_UNSIGNED_INT},
    {"unsignedShort", DT_UNSIGNED_SHORT},
    {"unsignedByte", DT_UNSIGNED_BYTE},
    {"float", DT_FLOAT},
    {"double", DT_DOUBLE},
    {"date", DT_DATE},
    {"dateTime", DT_DATE_TIME},
    {"time", DT_TIME},
    {"duration", DT_DURATION},
    {"anyURI", DT_ANY_URI},
    {"QName", DT_QNAME},
    {"NCName", DT_NCNAME},
    {"Name", DT_NAME},
    {"NMTOKEN", DT_NMTOKEN},
    {"language", DT_LANGUAGE},
    {"hexBinary", DT_HEX_BINARY},
    {"base64Binary", DT_BASE64_BINARY},
    {"anySimpleType", DT_ANY_SIMPLE_TYPE},
};

static int dt_int_in_range(int id, int negative, const Py_UCS4 *digits, Py_ssize_t digit_len);

static int dt_lookup(const Py_UCS4 *name, Py_ssize_t len) {
    for (size_t index = 0; index < sizeof(DT_NAMES) / sizeof(DT_NAMES[0]); index++) {
        if (u_eq_ascii(name, len, DT_NAMES[index].name)) {
            return DT_NAMES[index].id;
        }
    }
    return DT_UNKNOWN;
}

/* preserve = 0, replace = 1, collapse = 2. */
static int dt_default_ws(int id) {
    if (id == DT_STRING) {
        return 0;
    }
    if (id == DT_NORMALIZED_STRING) {
        return 1;
    }
    return 2;
}

/* Normalize `value` per the whitespace mode into an arena buffer, returning it and
   its length. collapse trims and folds internal runs to a single space. */
static const Py_UCS4 *dt_normalize_ws(arena *mem, int ws, const Py_UCS4 *value, Py_ssize_t len, Py_ssize_t *out_len) {
    if (ws == 0) {
        *out_len = len;
        return value;
    }
    Py_UCS4 *buffer = arena_alloc(mem, (size_t)(len + 1) * sizeof(Py_UCS4));
    if (buffer == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        *out_len = len;   /* GCOVR_EXCL_LINE */
        return value;     /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t out = 0;
    if (ws == 1) {
        for (Py_ssize_t index = 0; index < len; index++) {
            buffer[out++] = is_xml_space(value[index]) ? ' ' : value[index];
        }
    } else {
        Py_ssize_t index = 0;
        while (index < len && is_xml_space(value[index])) {
            index++;
        }
        int pending = 0;
        for (; index < len; index++) {
            if (is_xml_space(value[index])) {
                pending = 1;
                continue;
            }
            if (pending) {
                buffer[out++] = ' ';
                pending = 0;
            }
            buffer[out++] = value[index];
        }
    }
    buffer[out] = 0;
    *out_len = out;
    return buffer;
}

/* ---- lexical-space checks ---- */

static int is_digit(Py_UCS4 codepoint) {
    return codepoint >= '0' && codepoint <= '9';
}

static int is_ascii_alpha(Py_UCS4 codepoint) {
    return (codepoint >= 'a' && codepoint <= 'z') || (codepoint >= 'A' && codepoint <= 'Z');
}

/* An XML NameStartChar (ASCII letter / _ ; non-ASCII accepted wholesale). */
static int is_name_start(Py_UCS4 codepoint) {
    if (is_ascii_alpha(codepoint)) {
        return 1;
    }
    if (codepoint == '_') {
        return 1;
    }
    return codepoint >= 0x80;
}

static int is_name_char(Py_UCS4 codepoint) {
    if (is_name_start(codepoint) || is_digit(codepoint)) {
        return 1;
    }
    return codepoint == '-' || codepoint == '.';
}

/* NMTOKEN: one or more NameChars (a NameChar may be a colon). */
static int lex_nmtoken(const Py_UCS4 *value, Py_ssize_t len) {
    if (len == 0) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        if (!is_name_char(value[index]) && value[index] != ':') {
            return 0;
        }
    }
    return 1;
}

/* NCName: a Name with no colon. */
static int lex_ncname(const Py_UCS4 *value, Py_ssize_t len) {
    if (len == 0 || !is_name_start(value[0])) {
        return 0;
    }
    for (Py_ssize_t index = 1; index < len; index++) {
        if (!is_name_char(value[index])) {
            return 0;
        }
    }
    return 1;
}

static int lex_name(const Py_UCS4 *value, Py_ssize_t len) {
    if (len == 0 || (!is_name_start(value[0]) && value[0] != ':')) {
        return 0;
    }
    for (Py_ssize_t index = 1; index < len; index++) {
        if (!is_name_char(value[index]) && value[index] != ':') {
            return 0;
        }
    }
    return 1;
}

static int lex_qname(const Py_UCS4 *value, Py_ssize_t len) {
    const Py_UCS4 *local, *prefix;
    Py_ssize_t local_len = 0, prefix_len = 0;
    split_prefix(value, len, &local, &local_len, &prefix, &prefix_len);
    if (prefix_len == 0) {
        return lex_ncname(local, local_len);
    }
    return lex_ncname(prefix, prefix_len) && lex_ncname(local, local_len);
}

static int lex_boolean(const Py_UCS4 *value, Py_ssize_t len) {
    static const char *const literals[] = {"true", "false", "1", "0"};
    for (size_t index = 0; index < sizeof(literals) / sizeof(literals[0]); index++) {
        if (u_eq_ascii(value, len, literals[index])) {
            return 1;
        }
    }
    return 0;
}

/* An optionally signed run of digits, returning its digit span for the integer
   subtype range checks. */
static int lex_integer(const Py_UCS4 *value, Py_ssize_t len, int *negative, const Py_UCS4 **digits,
                       Py_ssize_t *digit_len) {
    Py_ssize_t index = 0;
    *negative = 0;
    if (index < len && (value[index] == '+' || value[index] == '-')) {
        *negative = value[index] == '-';
        index++;
    }
    Py_ssize_t start = index;
    while (index < len && is_digit(value[index])) {
        index++;
    }
    if (index == start || index != len) {
        return 0;
    }
    while (start + 1 < index && value[start] == '0') { /* strip leading zeros for the range compare */
        start++;
    }
    *digits = value + start;
    *digit_len = index - start;
    return 1;
}

static int lex_decimal(const Py_UCS4 *value, Py_ssize_t len) {
    Py_ssize_t index = 0;
    if (index < len && (value[index] == '+' || value[index] == '-')) {
        index++;
    }
    Py_ssize_t digits = 0;
    while (index < len && is_digit(value[index])) {
        index++;
        digits++;
    }
    if (index < len && value[index] == '.') {
        index++;
        while (index < len && is_digit(value[index])) {
            index++;
            digits++;
        }
    }
    return digits > 0 && index == len;
}

static int lex_double(const Py_UCS4 *value, Py_ssize_t len) {
    if (u_eq_ascii(value, len, "INF") || u_eq_ascii(value, len, "-INF") || u_eq_ascii(value, len, "NaN")) {
        return 1;
    }
    Py_ssize_t index = 0;
    if (index < len && (value[index] == '+' || value[index] == '-')) {
        index++;
    }
    Py_ssize_t digits = 0;
    while (index < len && is_digit(value[index])) {
        index++;
        digits++;
    }
    if (index < len && value[index] == '.') {
        index++;
        while (index < len && is_digit(value[index])) {
            index++;
            digits++;
        }
    }
    if (digits == 0) {
        return 0;
    }
    if (index < len && (value[index] == 'e' || value[index] == 'E')) {
        index++;
        if (index < len && (value[index] == '+' || value[index] == '-')) {
            index++;
        }
        Py_ssize_t exp_digits = 0;
        while (index < len && is_digit(value[index])) {
            index++;
            exp_digits++;
        }
        if (exp_digits == 0) {
            return 0;
        }
    }
    return index == len;
}

/* n fixed-width digits whose value lands in [lo, hi]. */
static int fixed_digits(const Py_UCS4 *value, Py_ssize_t at, Py_ssize_t count, int lo, int hi) {
    int total = 0;
    for (Py_ssize_t index = 0; index < count; index++) {
        if (!is_digit(value[at + index])) {
            return -1;
        }
        total = total * 10 + (int)(value[at + index] - '0');
    }
    return total >= lo && total <= hi ? total : -1;
}

/* An optional trailing timezone: "Z" or (+|-)hh:mm. */
static int lex_timezone(const Py_UCS4 *value, Py_ssize_t at, Py_ssize_t len) {
    if (at == len) {
        return 1;
    }
    if (value[at] == 'Z') {
        return at + 1 == len;
    }
    if (value[at] != '+' && value[at] != '-') {
        return 0;
    }
    if (len - at != 6) {
        return 0;
    }
    if (value[at + 3] != ':') {
        return 0;
    }
    if (fixed_digits(value, at + 1, 2, 0, 23) < 0) {
        return 0;
    }
    return fixed_digits(value, at + 4, 2, 0, 59) >= 0;
}

static int lex_time_of_day(const Py_UCS4 *value, Py_ssize_t at, Py_ssize_t len, Py_ssize_t *end) {
    if (len - at < 8) {
        return 0;
    }
    if (value[at + 2] != ':' || value[at + 5] != ':') {
        return 0;
    }
    if (fixed_digits(value, at, 2, 0, 23) < 0 || fixed_digits(value, at + 3, 2, 0, 59) < 0) {
        return 0;
    }
    if (fixed_digits(value, at + 6, 2, 0, 59) < 0) {
        return 0;
    }
    Py_ssize_t index = at + 8;
    if (index < len && value[index] == '.') {
        index++;
        Py_ssize_t frac = 0;
        while (index < len && is_digit(value[index])) {
            index++;
            frac++;
        }
        if (frac == 0) {
            return 0;
        }
    }
    *end = index;
    return 1;
}

static int lex_date_body(const Py_UCS4 *value, Py_ssize_t len, Py_ssize_t *end) {
    Py_ssize_t index = 0;
    if (index < len && value[index] == '-') { /* a negative (BCE) year is allowed */
        index++;
    }
    Py_ssize_t year_start = index;
    while (index < len && is_digit(value[index])) {
        index++;
    }
    if (index - year_start < 4 || len - index < 6) {
        return 0;
    }
    if (value[index] != '-' || value[index + 3] != '-') {
        return 0;
    }
    if (fixed_digits(value, index + 1, 2, 1, 12) < 0 || fixed_digits(value, index + 4, 2, 1, 31) < 0) {
        return 0;
    }
    *end = index + 6;
    return 1;
}

static int lex_date(const Py_UCS4 *value, Py_ssize_t len) {
    Py_ssize_t end = 0;
    if (!lex_date_body(value, len, &end)) {
        return 0;
    }
    return lex_timezone(value, end, len);
}

static int lex_datetime(const Py_UCS4 *value, Py_ssize_t len) {
    Py_ssize_t end = 0;
    if (!lex_date_body(value, len, &end)) {
        return 0;
    }
    if (end >= len || value[end] != 'T') {
        return 0;
    }
    Py_ssize_t time_end = 0;
    if (!lex_time_of_day(value, end + 1, len, &time_end)) {
        return 0;
    }
    return lex_timezone(value, time_end, len);
}

static int lex_time(const Py_UCS4 *value, Py_ssize_t len) {
    Py_ssize_t end = 0;
    if (!lex_time_of_day(value, 0, len, &end)) {
        return 0;
    }
    return lex_timezone(value, end, len);
}

/* Whether `unit` is one of the three duration unit letters valid in this section (the
   date part before T, or the time part after it). */
static int duration_unit_ok(int in_time, Py_UCS4 unit) {
    const char *units = in_time ? "HMS" : "YMD";
    for (int index = 0; index < 3; index++) {
        if (unit == (Py_UCS4)(unsigned char)units[index]) {
            return 1;
        }
    }
    return 0;
}

/* duration: -?PnYnMnDTnHnMnS with at least one component and T only when a time part
   follows. */
static int lex_duration(const Py_UCS4 *value, Py_ssize_t len) {
    Py_ssize_t index = 0;
    if (index < len && value[index] == '-') {
        index++;
    }
    if (index >= len || value[index] != 'P') {
        return 0;
    }
    index++;
    int seen = 0, in_time = 0;
    while (index < len) {
        if (value[index] == 'T') {
            if (in_time) {
                return 0;
            }
            in_time = 1;
            index++;
            continue;
        }
        Py_ssize_t digits = 0;
        while (index < len && is_digit(value[index])) {
            index++;
            digits++;
        }
        if (digits == 0 || index >= len) {
            return 0;
        }
        if (!duration_unit_ok(in_time, value[index])) {
            return 0;
        }
        seen = 1;
        index++;
    }
    /* the loop advances by whole components, so it can only exit with index == len */
    return seen;
}

static int is_alnum(Py_UCS4 codepoint) {
    return is_ascii_alpha(codepoint) || is_digit(codepoint);
}

static int lex_language(const Py_UCS4 *value, Py_ssize_t len) {
    Py_ssize_t index = 0, run = 0;
    while (index < len && is_ascii_alpha(value[index])) {
        index++;
        run++;
    }
    if (run < 1 || run > 8) {
        return 0;
    }
    while (index < len) {
        if (value[index] != '-') {
            return 0;
        }
        index++;
        run = 0;
        while (index < len && is_alnum(value[index])) {
            index++;
            run++;
        }
        if (run < 1 || run > 8) {
            return 0;
        }
    }
    return 1;
}

static int is_hex_digit(Py_UCS4 codepoint) {
    if (is_digit(codepoint)) {
        return 1;
    }
    if (codepoint >= 'a' && codepoint <= 'f') {
        return 1;
    }
    return codepoint >= 'A' && codepoint <= 'F';
}

static int lex_hex_binary(const Py_UCS4 *value, Py_ssize_t len) {
    if (len % 2 != 0) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        if (!is_hex_digit(value[index])) {
            return 0;
        }
    }
    return 1;
}

static int is_base64_char(Py_UCS4 codepoint) {
    if (is_ascii_alpha(codepoint) || is_digit(codepoint)) {
        return 1;
    }
    return codepoint == '+' || codepoint == '/';
}

static int lex_base64_binary(const Py_UCS4 *value, Py_ssize_t len) {
    Py_ssize_t count = 0, pad = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        if (is_xml_space(value[index])) {
            continue;
        }
        if (value[index] == '=') {
            pad++;
        } else if (pad > 0 || !is_base64_char(value[index])) {
            return 0;
        }
        count++;
    }
    return count % 4 == 0 && pad <= 2;
}

/* Whole-value lexical check for a built-in datatype. */
static int dt_check_lexical(int id, const Py_UCS4 *value, Py_ssize_t len) {
    int negative;
    const Py_UCS4 *digits;
    Py_ssize_t digit_len = 0;
    switch (id) {
    case DT_STRING:
    case DT_NORMALIZED_STRING:
    case DT_TOKEN:
    case DT_ANY_URI:
    case DT_ANY_SIMPLE_TYPE:
        return 1;
    case DT_BOOLEAN:
        return lex_boolean(value, len);
    case DT_DECIMAL:
        return lex_decimal(value, len);
    case DT_FLOAT:
    case DT_DOUBLE:
        return lex_double(value, len);
    case DT_DATE:
        return lex_date(value, len);
    case DT_DATE_TIME:
        return lex_datetime(value, len);
    case DT_TIME:
        return lex_time(value, len);
    case DT_DURATION:
        return lex_duration(value, len);
    case DT_QNAME:
        return lex_qname(value, len);
    case DT_NCNAME:
        return lex_ncname(value, len);
    case DT_NAME:
        return lex_name(value, len);
    case DT_NMTOKEN:
        return lex_nmtoken(value, len);
    case DT_LANGUAGE:
        return lex_language(value, len);
    case DT_HEX_BINARY:
        return lex_hex_binary(value, len);
    case DT_BASE64_BINARY:
        return lex_base64_binary(value, len);
    default:
        break;
    }
    /* the integer family: lexical integer plus a subtype range check */
    if (!lex_integer(value, len, &negative, &digits, &digit_len)) {
        return 0;
    }
    return dt_int_in_range(id, negative, digits, digit_len);
}

/* Whether an integer given as (sign, leading-zero-stripped digit span) lies in the
   subtype's value range. Comparisons are on the digit string so unsignedLong's 2^64-1
   bound holds without a wider integer type. */
static int dt_cmp_bound(int negative, const Py_UCS4 *digits, Py_ssize_t digit_len, const char *bound) {
    int bound_neg = bound[0] == '-';
    const char *bound_digits = bound_neg ? bound + 1 : bound;
    Py_ssize_t bound_len = (Py_ssize_t)strlen(bound_digits);
    if (negative != bound_neg) {
        return negative ? -1 : 1; /* negative < non-negative */
    }
    int magnitude;
    if (digit_len != bound_len) {
        magnitude = digit_len < bound_len ? -1 : 1;
    } else {
        magnitude = 0;
        for (Py_ssize_t index = 0; index < digit_len && magnitude == 0; index++) {
            char self = (char)digits[index];
            magnitude = self < bound_digits[index] ? -1 : (self > bound_digits[index] ? 1 : 0);
        }
    }
    return negative ? -magnitude : magnitude;
}

/* The inclusive value range of a bounded integer subtype (NULL bound = unbounded on that
   side). An id not listed (integer) has no bounds. */
typedef struct {
    int id;
    const char *lo;
    const char *hi;
} int_range;

static const int_range INT_RANGES[] = {
    {DT_LONG, "-9223372036854775808", "9223372036854775807"},
    {DT_INT, "-2147483648", "2147483647"},
    {DT_SHORT, "-32768", "32767"},
    {DT_BYTE, "-128", "127"},
    {DT_NON_NEGATIVE_INTEGER, "0", NULL},
    {DT_POSITIVE_INTEGER, "1", NULL},
    {DT_NON_POSITIVE_INTEGER, NULL, "0"},
    {DT_NEGATIVE_INTEGER, NULL, "-1"},
    {DT_UNSIGNED_LONG, "0", "18446744073709551615"},
    {DT_UNSIGNED_INT, "0", "4294967295"},
    {DT_UNSIGNED_SHORT, "0", "65535"},
    {DT_UNSIGNED_BYTE, "0", "255"},
};

static int dt_int_in_range(int id, int negative, const Py_UCS4 *digits, Py_ssize_t digit_len) {
    const char *lo = NULL, *hi = NULL;
    for (size_t index = 0; index < sizeof(INT_RANGES) / sizeof(INT_RANGES[0]); index++) {
        if (INT_RANGES[index].id == id) {
            lo = INT_RANGES[index].lo;
            hi = INT_RANGES[index].hi;
            break;
        }
    }
    if (lo != NULL && dt_cmp_bound(negative, digits, digit_len, lo) < 0) {
        return 0;
    }
    if (hi != NULL && dt_cmp_bound(negative, digits, digit_len, hi) > 0) {
        return 0;
    }
    return 1;
}

static int dt_is_numeric(int id) {
    return id == DT_DECIMAL || id == DT_FLOAT || id == DT_DOUBLE || (id >= DT_INTEGER && id <= DT_UNSIGNED_BYTE);
}

static double dt_to_double(const Py_UCS4 *value, Py_ssize_t len) {
    char buffer[64];
    Py_ssize_t out = 0;
    for (Py_ssize_t index = 0; index < len && out < (Py_ssize_t)sizeof(buffer) - 1; index++) {
        /* only ever called on a numeric value that already passed its ASCII lexical check */
        buffer[out++] = value[index] < 0x80 ? (char)value[index] : '?'; /* GCOVR_EXCL_BR_LINE */
    }
    buffer[out] = 0;
    return strtod(buffer, NULL);
}

/* ---- facets ---- */

typedef struct {
    const Py_UCS4 *ptr;
    Py_ssize_t len;
} strspan;

typedef struct {
    int base_id;
    int ws;
    int has_length, length;
    int has_min_length, min_length;
    int has_max_length, max_length;
    int has_min_inclusive;
    double min_inclusive;
    int has_max_inclusive;
    double max_inclusive;
    int has_min_exclusive;
    double min_exclusive;
    int has_max_exclusive;
    double max_exclusive;
    int has_total_digits, total_digits;
    int has_fraction_digits, fraction_digits;
    strspan *enums;
    Py_ssize_t enum_count, enum_cap;
    strspan *patterns;
    Py_ssize_t pattern_count, pattern_cap;
} facetset;

static void facetset_init(facetset *facets, int base_id) {
    memset(facets, 0, sizeof(*facets));
    facets->base_id = base_id;
    facets->ws = dt_default_ws(base_id);
}

static int strspan_push(arena *mem, strspan **items, Py_ssize_t *count, Py_ssize_t *cap, const Py_UCS4 *ptr,
                        Py_ssize_t len) {
    if (*count == *cap) {
        Py_ssize_t next = *cap ? *cap * 2 : 4;
        strspan *grown = arena_alloc(mem, (size_t)next * sizeof(strspan));
        if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return -1;       /* GCOVR_EXCL_LINE */
        }
        if (*count > 0) {
            memcpy(grown, *items, (size_t)*count * sizeof(strspan));
        }
        *items = grown;
        *cap = next;
    }
    (*items)[*count].ptr = ptr;
    (*items)[(*count)++].len = len;
    return 0;
}

/* Fold one facet (name, value) into the set. */
static int facet_add(arena *mem, facetset *facets, const Py_UCS4 *name, Py_ssize_t name_len, const Py_UCS4 *value,
                     Py_ssize_t value_len) {
    if (u_eq_ascii(name, name_len, "enumeration")) {
        return strspan_push(mem, &facets->enums, &facets->enum_count, &facets->enum_cap, value, value_len);
    }
    if (u_eq_ascii(name, name_len, "pattern")) {
        return strspan_push(mem, &facets->patterns, &facets->pattern_count, &facets->pattern_cap, value, value_len);
    }
    if (u_eq_ascii(name, name_len, "whiteSpace")) {
        facets->ws = u_eq_ascii(value, value_len, "replace") ? 1 : (u_eq_ascii(value, value_len, "collapse") ? 2 : 0);
    } else if (u_eq_ascii(name, name_len, "length")) {
        facets->has_length = 1;
        facets->length = (int)dt_to_double(value, value_len);
    } else if (u_eq_ascii(name, name_len, "minLength")) {
        facets->has_min_length = 1;
        facets->min_length = (int)dt_to_double(value, value_len);
    } else if (u_eq_ascii(name, name_len, "maxLength")) {
        facets->has_max_length = 1;
        facets->max_length = (int)dt_to_double(value, value_len);
    } else if (u_eq_ascii(name, name_len, "minInclusive")) {
        facets->has_min_inclusive = 1;
        facets->min_inclusive = dt_to_double(value, value_len);
    } else if (u_eq_ascii(name, name_len, "maxInclusive")) {
        facets->has_max_inclusive = 1;
        facets->max_inclusive = dt_to_double(value, value_len);
    } else if (u_eq_ascii(name, name_len, "minExclusive")) {
        facets->has_min_exclusive = 1;
        facets->min_exclusive = dt_to_double(value, value_len);
    } else if (u_eq_ascii(name, name_len, "maxExclusive")) {
        facets->has_max_exclusive = 1;
        facets->max_exclusive = dt_to_double(value, value_len);
    } else if (u_eq_ascii(name, name_len, "totalDigits")) {
        facets->has_total_digits = 1;
        facets->total_digits = (int)dt_to_double(value, value_len);
    } else if (u_eq_ascii(name, name_len, "fractionDigits")) {
        facets->has_fraction_digits = 1;
        facets->fraction_digits = (int)dt_to_double(value, value_len);
    }
    return 0;
}

static void count_digits(const Py_UCS4 *value, Py_ssize_t len, int *total, int *fraction) {
    *total = 0;
    *fraction = 0;
    int after_point = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        if (value[index] == '.') {
            after_point = 1;
        } else if (is_digit(value[index])) {
            (*total)++;
            if (after_point) {
                (*fraction)++;
            }
        }
    }
}

#include "validate/regex.h"

/* Apply every facet to the whitespace-normalized value, reporting each violation on
   `node`. Returns 1 when all pass. */
static int facet_check(valctx *ctx, th_node *node, const facetset *facets, const Py_UCS4 *value, Py_ssize_t len) {
    int ok = 1;
    if (facets->has_length && len != facets->length) {
        report(ctx, node, "facet", "value length %zd does not equal length facet %d", len, facets->length);
        ok = 0;
    }
    if (facets->has_min_length && len < facets->min_length) {
        report(ctx, node, "facet", "value length %zd is below minLength %d", len, facets->min_length);
        ok = 0;
    }
    if (facets->has_max_length && len > facets->max_length) {
        report(ctx, node, "facet", "value length %zd exceeds maxLength %d", len, facets->max_length);
        ok = 0;
    }
    if (dt_is_numeric(facets->base_id)) {
        double number = dt_to_double(value, len);
        if (facets->has_min_inclusive && number < facets->min_inclusive) {
            report(ctx, node, "facet", "value is below minInclusive");
            ok = 0;
        }
        if (facets->has_max_inclusive && number > facets->max_inclusive) {
            report(ctx, node, "facet", "value is above maxInclusive");
            ok = 0;
        }
        if (facets->has_min_exclusive && number <= facets->min_exclusive) {
            report(ctx, node, "facet", "value is not above minExclusive");
            ok = 0;
        }
        if (facets->has_max_exclusive && number >= facets->max_exclusive) {
            report(ctx, node, "facet", "value is not below maxExclusive");
            ok = 0;
        }
    }
    if (facets->has_total_digits || facets->has_fraction_digits) {
        int total, fraction;
        count_digits(value, len, &total, &fraction);
        if (facets->has_total_digits && total > facets->total_digits) {
            report(ctx, node, "facet", "value has more than totalDigits %d significant digits", facets->total_digits);
            ok = 0;
        }
        if (facets->has_fraction_digits && fraction > facets->fraction_digits) {
            report(ctx, node, "facet", "value has more than fractionDigits %d fraction digits",
                   facets->fraction_digits);
            ok = 0;
        }
    }
    if (facets->enum_count > 0) {
        int matched = 0;
        for (Py_ssize_t index = 0; index < facets->enum_count && !matched; index++) {
            if (u_eq_u(value, len, facets->enums[index].ptr, facets->enums[index].len)) {
                matched = 1;
            }
        }
        if (!matched) {
            report(ctx, node, "facet", "value is not one of the enumerated values");
            ok = 0;
        }
    }
    for (Py_ssize_t index = 0; index < facets->pattern_count; index++) {
        if (!regex_full_match(&ctx->schema->mem, facets->patterns[index].ptr, facets->patterns[index].len, value,
                              len)) {
            report(ctx, node, "facet", "value does not match the required pattern");
            ok = 0;
        }
    }
    return ok;
}

#endif /* TURBOHTML_VALIDATE_DATATYPES_H */
