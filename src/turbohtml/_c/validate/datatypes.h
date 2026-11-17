/* Built-in XSD datatypes and constraining facets, shared by the XSD engine and the
   RELAX NG <data> element when it names the XSD datatype library. Included once into
   validate/schema.c; every definition is static. Lexical spaces follow XSD 1.0 part 2;
   numeric facet bounds compare as doubles, which is exact for the value ranges a real
   schema constrains; date, dateTime, time, duration and g* bounds compare by the order
   relation XSD gives each type.

   whiteSpace runs first (preserve / replace / collapse), then the value is checked
   against its datatype's lexical space, then every constraining facet. */

#ifndef TURBOHTML_VALIDATE_DATATYPES_H
#define TURBOHTML_VALIDATE_DATATYPES_H

#include "tokenizer/xml_names.h"

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
    DT_ID,
    DT_IDREF,
    DT_ENTITY,
    DT_NOTATION,
    DT_G_YEAR,
    DT_G_YEAR_MONTH,
    DT_G_MONTH,
    DT_G_MONTH_DAY,
    DT_G_DAY,
    DT_NMTOKENS,
    DT_IDREFS,
    DT_ENTITIES,
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
    {"ID", DT_ID},
    {"IDREF", DT_IDREF},
    {"ENTITY", DT_ENTITY},
    {"NOTATION", DT_NOTATION},
    {"gYear", DT_G_YEAR},
    {"gYearMonth", DT_G_YEAR_MONTH},
    {"gMonth", DT_G_MONTH},
    {"gMonthDay", DT_G_MONTH_DAY},
    {"gDay", DT_G_DAY},
    {"NMTOKENS", DT_NMTOKENS},
    {"IDREFS", DT_IDREFS},
    {"ENTITIES", DT_ENTITIES},
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

/* The built-in list types: their values are whitespace-separated items, each of the datatype dt_check_lexical gives
   them, and at least one item. */
static int dt_is_list(int id) {
    return id == DT_NMTOKENS || id == DT_IDREFS || id == DT_ENTITIES;
}

/* The items of a list value, which the length facets of a list count: its runs of non-whitespace. */
static Py_ssize_t dt_list_count(const Py_UCS4 *value, Py_ssize_t len) {
    Py_ssize_t count = 0;
    int in_item = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        if (is_xml_space(value[index])) {
            in_item = 0;
        } else if (!in_item) {
            in_item = 1;
            count++;
        }
    }
    return count;
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

/* Normalize `value` per the whitespace mode into `buffer` (room for len + 1 code points) and return the length. */
static Py_ssize_t dt_ws_fill(Py_UCS4 *buffer, int ws, const Py_UCS4 *value, Py_ssize_t len) {
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
    return out;
}

/* A compiled <value> is read on every later check, so it gets its own arena copy rather than the per-call buffer. */
static const Py_UCS4 *dt_normalize_ws_arena(arena *mem, int ws, const Py_UCS4 *value, Py_ssize_t len,
                                            Py_ssize_t *out_len) {
    if (ws == 0) {
        *out_len = len;
        return value;
    }
    Py_UCS4 *buffer = arena_alloc(mem, (size_t)(len + 1) * sizeof(Py_UCS4));
    if (buffer == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
        *out_len = len;   /* GCOVR_EXCL_LINE */
        return value;     /* GCOVR_EXCL_LINE */
    }
    *out_len = dt_ws_fill(buffer, ws, value, len);
    return buffer;
}

/* Normalize `value` per the whitespace mode, returning it and its length. collapse trims and folds internal runs to a
   single space. A value the mode rewrites goes into the one buffer a validate() call keeps for this, so the result
   lasts until the next normalization: a RELAX NG text is checked once for every <data> alternative it meets, and the
   arena, which releases nothing until validation ends, would otherwise keep a copy of it for each. */
static const Py_UCS4 *dt_normalize_ws(th_schema *schema, int ws, const Py_UCS4 *value, Py_ssize_t len,
                                      Py_ssize_t *out_len) {
    if (ws == 0) {
        *out_len = len;
        return value;
    }
    if (len >= schema->ws_cap) {
        /* at least doubling keeps the buffers outgrown on the way, which stay in the arena, below the one in use */
        const Py_ssize_t cap = len >= schema->ws_cap * 2 ? len + 1 : schema->ws_cap * 2;
        Py_UCS4 *grown = arena_alloc(&schema->mem, (size_t)cap * sizeof(Py_UCS4));
        if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            *out_len = len;  /* GCOVR_EXCL_LINE */
            return value;    /* GCOVR_EXCL_LINE */
        }
        schema->ws_buffer = grown;
        schema->ws_cap = cap;
    }
    *out_len = dt_ws_fill(schema->ws_buffer, ws, value, len);
    return schema->ws_buffer;
}

/* ---- lexical-space checks ---- */

static int is_digit(Py_UCS4 codepoint) {
    return codepoint >= '0' && codepoint <= '9';
}

static int is_ascii_alpha(Py_UCS4 codepoint) {
    return (codepoint >= 'a' && codepoint <= 'z') || (codepoint >= 'A' && codepoint <= 'Z');
}

/* NMTOKEN: one or more NameChars (a NameChar may be a colon). */
static int lex_nmtoken(const Py_UCS4 *value, Py_ssize_t len) {
    if (len == 0) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        if (!is_name_char(value[index])) {
            return 0;
        }
    }
    return 1;
}

/* NCName: a Name with no colon. */
static int lex_ncname(const Py_UCS4 *value, Py_ssize_t len) {
    if (len == 0 || value[0] == ':' || !is_name_start(value[0])) {
        return 0;
    }
    for (Py_ssize_t index = 1; index < len; index++) {
        if (value[index] == ':' || !is_name_char(value[index])) {
            return 0;
        }
    }
    return 1;
}

static int lex_name(const Py_UCS4 *value, Py_ssize_t len) {
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

/* The year has no length limit, so its residue mod 400 is folded digit by digit: the leap rule
   depends on nothing else. */
static int year_is_leap(const Py_UCS4 *digits, Py_ssize_t len) {
    int residue = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        residue = (residue * 10 + (int)(digits[index] - '0')) % 400;
    }
    return residue % 4 == 0 && (residue % 100 != 0 || residue == 0);
}

/* The days in `month` of the year written by `year`. Only February depends on the year, so only it reads the year. */
static int days_in_month(int month, const Py_UCS4 *year, Py_ssize_t year_len) {
    static const int DAYS[] = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
    return month == 2 && year_is_leap(year, year_len) ? 29 : DAYS[month - 1];
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
    const int month = fixed_digits(value, index + 1, 2, 1, 12);
    const int day = fixed_digits(value, index + 4, 2, 1, 31);
    if (month < 0 || day < 0 || day > days_in_month(month, value + year_start, index - year_start)) {
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
   follows. Only the seconds take a fraction, and it needs a digit on each side of the point. */
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
        int fractional = value[index] == '.';
        if (fractional) {
            index++;
            Py_ssize_t fraction_digits = 0;
            while (index < len && is_digit(value[index])) {
                index++;
                fraction_digits++;
            }
            if (fraction_digits == 0 || index >= len) {
                return 0;
            }
        }
        if (!duration_unit_ok(in_time, value[index]) || (fractional && value[index] != 'S')) {
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

/* An optional minus and at least four digits; `end` is where the digits stop. */
static int lex_year_end(const Py_UCS4 *value, Py_ssize_t len, Py_ssize_t *end) {
    Py_ssize_t index = 0;
    if (index < len && value[index] == '-') {
        index++;
    }
    Py_ssize_t year_start = index;
    while (index < len && is_digit(value[index])) {
        index++;
    }
    if (index - year_start < 4) {
        return 0;
    }
    *end = index;
    return 1;
}

static int lex_gyear(const Py_UCS4 *value, Py_ssize_t len) {
    Py_ssize_t end = 0;
    return lex_year_end(value, len, &end) && lex_timezone(value, end, len);
}

static int lex_gyear_month(const Py_UCS4 *value, Py_ssize_t len) {
    Py_ssize_t end = 0;
    if (!lex_year_end(value, len, &end) || len - end < 3 || value[end] != '-') {
        return 0;
    }
    return fixed_digits(value, end + 1, 2, 1, 12) >= 0 && lex_timezone(value, end + 3, len);
}

static int lex_gmonth(const Py_UCS4 *value, Py_ssize_t len) {
    if (len < 4 || value[0] != '-' || value[1] != '-') {
        return 0;
    }
    return fixed_digits(value, 2, 2, 1, 12) >= 0 && lex_timezone(value, 4, len);
}

/* The value names no year, so February takes its leap day. */
static int lex_gmonth_day(const Py_UCS4 *value, Py_ssize_t len) {
    if (len < 7 || value[0] != '-' || value[1] != '-' || value[4] != '-') {
        return 0;
    }
    const int month = fixed_digits(value, 2, 2, 1, 12);
    const int day = fixed_digits(value, 5, 2, 1, 31);
    return month >= 0 && day >= 0 && day <= days_in_month(month, NULL, 0) && lex_timezone(value, 7, len);
}

static int lex_gday(const Py_UCS4 *value, Py_ssize_t len) {
    if (len < 5 || value[0] != '-' || value[1] != '-' || value[2] != '-') {
        return 0;
    }
    return fixed_digits(value, 3, 2, 1, 31) >= 0 && lex_timezone(value, 5, len);
}

static int dt_check_lexical(int id, const Py_UCS4 *value, Py_ssize_t len);

/* Whether every whitespace-separated item of `value` has the lexical space of `item`, and there is at least one. */
static int lex_list(int item, const Py_UCS4 *value, Py_ssize_t len) {
    Py_ssize_t count = 0;
    for (Py_ssize_t at = 0; at < len;) {
        if (is_xml_space(value[at])) {
            at++;
            continue;
        }
        Py_ssize_t end = at;
        while (end < len && !is_xml_space(value[end])) {
            end++;
        }
        if (!dt_check_lexical(item, value + at, end - at)) {
            return 0;
        }
        count++;
        at = end;
    }
    return count > 0;
}

/* Whole-value lexical check for a built-in datatype. */
static int dt_check_lexical(int id, const Py_UCS4 *value, Py_ssize_t len) {
    int negative;
    const Py_UCS4 *digits;
    Py_ssize_t digit_len = 0;
    switch (id) {
    case DT_NMTOKENS:
        return lex_list(DT_NMTOKEN, value, len);
    case DT_IDREFS:
    case DT_ENTITIES:
        return lex_list(DT_NCNAME, value, len);
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
    case DT_ID:
    case DT_IDREF:
    case DT_ENTITY:
        return lex_ncname(value, len);
    case DT_NOTATION:
        return lex_qname(value, len);
    case DT_G_YEAR:
        return lex_gyear(value, len);
    case DT_G_YEAR_MONTH:
        return lex_gyear_month(value, len);
    case DT_G_MONTH:
        return lex_gmonth(value, len);
    case DT_G_MONTH_DAY:
        return lex_gmonth_day(value, len);
    case DT_G_DAY:
        return lex_gday(value, len);
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

static int dt_is_float(int id) {
    return id == DT_FLOAT || id == DT_DOUBLE;
}

static int dt_is_decimal_family(int id) {
    return id == DT_DECIMAL || (id >= DT_INTEGER && id <= DT_UNSIGNED_BYTE);
}

/* `ascii` has room for len + 1 characters. */
static double dt_ascii_double(char *ascii, const Py_UCS4 *value, Py_ssize_t len) {
    for (Py_ssize_t index = 0; index < len; index++) {
        /* only ever called on a numeric value that already passed its ASCII lexical check */
        ascii[index] = value[index] < 0x80 ? (char)value[index] : '?'; /* GCOVR_EXCL_BR_LINE */
    }
    ascii[len] = 0;
    return strtod(ascii, NULL);
}

/* Converts the whole value. The lexical checks put no bound on a number's length, so leading zeros or a long fraction
   can push the digits or the exponent that decide a range comparison past any fixed window. A value too long for the
   stack buffer takes an arena copy: this is for a facet's bound, which the schema states once. */
static double dt_to_double(arena *mem, const Py_UCS4 *value, Py_ssize_t len) {
    char buffer[64];
    char *ascii = buffer;
    if (len >= (Py_ssize_t)sizeof(buffer)) {
        ascii = arena_alloc(mem, (size_t)len + 1);
        if (ascii == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return 0.0;      /* GCOVR_EXCL_LINE: no_memory makes the compile or validate entry raise MemoryError */
        }
    }
    return dt_ascii_double(ascii, value, len);
}

/* The same for a value under validation. A long one is copied into the one buffer a validate() call keeps for this,
   as dt_normalize_ws does: a RELAX NG text is converted once for every range-bounded <data> alternative it meets, and
   the arena would otherwise keep a copy of it for each. */
static double dt_value_double(th_schema *schema, const Py_UCS4 *value, Py_ssize_t len) {
    char buffer[64];
    if (len < (Py_ssize_t)sizeof(buffer)) {
        return dt_ascii_double(buffer, value, len);
    }
    if (len >= schema->number_cap) {
        const Py_ssize_t cap = len >= schema->number_cap * 2 ? len + 1 : schema->number_cap * 2;
        char *grown = arena_alloc(&schema->mem, (size_t)cap);
        if (grown == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return 0.0;      /* GCOVR_EXCL_LINE: no_memory makes the validate entry raise MemoryError */
        }
        schema->number_buffer = grown;
        schema->number_cap = cap;
    }
    return dt_ascii_double(schema->number_buffer, value, len);
}

static int dt_is_partial(int id) {
    return id >= DT_G_YEAR && id <= DT_G_DAY;
}

static int dt_is_ordered(int id) {
    return dt_is_decimal_family(id) || (id >= DT_DATE && id <= DT_DURATION) || dt_is_partial(id);
}

/* How two numbers stand: -1 below, 0 equal, 1 above, and 2 when they are unordered. A NaN is unordered. Past 2^53
   neighboring integers share a double, so two equal doubles there may stand for different values and are unordered
   too. */
static int dt_cmp(double left, double right) {
    if (left == right) {
        return fabs(left) < 0x1p53 ? 0 : 2;
    }
    return left < right ? -1 : (left > right ? 1 : 2);
}

/* Whether a float or double keeps one range facet of `side` (1 for a lower bound, -1 for an upper one). XML Schema
   makes NaN equal to itself and unordered with every other value, so a NaN bound admits only a NaN value, and only for
   an inclusive facet. */
static int dt_double_bound_ok(double number, double bound, int side, int inclusive) {
    int number_nan = isnan(number) != 0; /* GCOVR_EXCL_BR_LINE: dead type-dispatch arm of the isnan macro */
    int bound_nan = isnan(bound) != 0;   /* GCOVR_EXCL_BR_LINE: dead type-dispatch arm of the isnan macro */
    if (number_nan || bound_nan) {
        return inclusive && number_nan && bound_nan;
    }
    return (side > 0 ? number > bound : number < bound) || (inclusive && number == bound);
}

/* A decimal literal split into its sign and the digits before and after its point, with the zeros that carry no value
   removed: leading zeros of the whole part and trailing zeros of the fraction. */
typedef struct {
    int negative;
    const Py_UCS4 *whole, *fraction;
    Py_ssize_t whole_len, fraction_len;
} dt_decimal;

/* `value` has passed the lexical check, so it is non-empty and holds nothing after its whole part but the point. */
static dt_decimal dt_decimal_of(const Py_UCS4 *value, Py_ssize_t len) {
    dt_decimal number = {0};
    number.negative = value[0] == '-';
    Py_ssize_t index = value[0] == '+' || value[0] == '-';
    const Py_ssize_t start = index;
    while (index < len && is_digit(value[index])) {
        index++;
    }
    number.whole = value + start;
    number.whole_len = index - start;
    while (number.whole_len > 0 && number.whole[0] == '0') {
        number.whole++;
        number.whole_len--;
    }
    if (index < len) {
        number.fraction = value + index + 1;
        number.fraction_len = len - index - 1;
        while (number.fraction_len > 0 && number.fraction[number.fraction_len - 1] == '0') {
            number.fraction_len--;
        }
    }
    number.negative = number.negative && (number.whole_len > 0 || number.fraction_len > 0);
    return number;
}

/* The order of two digit runs read from the left, where a run that ends first sorts before a longer one it starts. */
static int dt_digits_cmp(const Py_UCS4 *left, Py_ssize_t left_len, const Py_UCS4 *right, Py_ssize_t right_len) {
    for (Py_ssize_t index = 0; index < left_len && index < right_len; index++) {
        if (left[index] != right[index]) {
            return left[index] < right[index] ? -1 : 1;
        }
    }
    return left_len == right_len ? 0 : (left_len < right_len ? -1 : 1);
}

/* The exact order of two decimal literals. A double cannot tell apart two long decimals that round alike, nor a tiny
   one from zero, so the digits are compared as written. */
static int dt_decimal_compare(const dt_decimal *a, const dt_decimal *b) {
    if (a->negative != b->negative) {
        return a->negative ? -1 : 1;
    }
    int magnitude;
    if (a->whole_len != b->whole_len) {
        magnitude = a->whole_len < b->whole_len ? -1 : 1;
    } else {
        magnitude = dt_digits_cmp(a->whole, a->whole_len, b->whole, b->whole_len);
    }
    if (magnitude == 0) {
        magnitude = dt_digits_cmp(a->fraction, a->fraction_len, b->fraction, b->fraction_len);
    }
    return a->negative ? -magnitude : magnitude;
}

/* The number the run of digits at `*at` writes, leaving `*at` on the character that ends the run or on `len`. A year
   and a duration's counts have no length limit and may open with any number of zeros, so the run is read whole. A gYear
   with no timezone is only its digits, so its run ends at `len` and must not read past it. */
static double dt_digit_run(const Py_UCS4 *value, Py_ssize_t len, Py_ssize_t *at) {
    double number = 0;
    while (*at < len && is_digit(value[*at])) {
        number = number * 10 + (double)(value[*at] - '0');
        (*at)++;
    }
    return number;
}

/* Days from 0000-03-01 to the first of the month that lies `months` months after January of year 0, in the proleptic
   Gregorian calendar. Doubles carry the whole-number arithmetic because the lexical checks bound neither a year nor
   a duration's counts: the result is exact for every date a real schema constrains. A count long enough to overflow
   comes out as NaN, and past 2^53 seconds distinct dates can round to one double; dt_cmp leaves all of these
   unordered. */
static double dt_month_days(double months) {
    months -= 2; /* a year counted from March ends on its leap day */
    double year = floor(months / 12), month = months - year * 12;
    double era = floor(year / 400), of_era = year - era * 400;
    return era * 146097 + of_era * 365 + floor(of_era / 4) - floor(of_era / 100) + floor((153 * month + 2) / 5);
}

/* A date, dateTime or time on the timeline: its whole seconds, in UTC when the value carries a timezone, and the
   digits of its fraction of a second, which the double has no bits left for once it holds a date. */
typedef struct {
    double seconds;
    const Py_UCS4 *fraction;
    Py_ssize_t fraction_len;
    int zoned;
} dt_moment;

/* Moves `moment` to UTC when the timezone that starts at `at` is present. */
static void dt_moment_zone(dt_moment *moment, const Py_UCS4 *value, Py_ssize_t at, Py_ssize_t len) {
    moment->zoned = at < len;
    if (moment->zoned && value[at] != 'Z') {
        const int offset = fixed_digits(value, at + 1, 2, 0, 23) * 3600 + fixed_digits(value, at + 4, 2, 0, 59) * 60;
        moment->seconds += value[at] == '-' ? offset : -offset;
    }
}

/* A g* value on the timeline. XSD 1.1 Part 2 appendix D.2.1 fills each absent field from 1972-12-31T00:00:00, and an
   absent day with the last day of its month, so a value's place depends only on the fields it states. `value` has
   passed dt_check_lexical for `id`. */
static dt_moment dt_partial_moment_of(int id, const Py_UCS4 *value, Py_ssize_t len) {
    dt_moment moment = {0, NULL, 0, 0};
    const int negative = value[0] == '-';
    Py_ssize_t year_end = negative;
    double year = 1972;
    if (id == DT_G_YEAR || id == DT_G_YEAR_MONTH) {
        year = dt_digit_run(value, len, &year_end);
        year = negative ? -year : year;
    }
    int month = 12, day = 31;
    Py_ssize_t zone = year_end;
    switch (id) {
    case DT_G_YEAR:
        break;
    case DT_G_YEAR_MONTH:
        month = fixed_digits(value, year_end + 1, 2, 1, 12);
        day = days_in_month(month, value + negative, year_end - negative);
        zone = year_end + 3;
        break;
    case DT_G_MONTH:
        month = fixed_digits(value, 2, 2, 1, 12);
        day = days_in_month(month, NULL, 0);
        zone = 4;
        break;
    case DT_G_MONTH_DAY:
        month = fixed_digits(value, 2, 2, 1, 12);
        day = fixed_digits(value, 5, 2, 1, 31);
        zone = 7;
        break;
    default:
        day = fixed_digits(value, 3, 2, 1, 31);
        zone = 5;
        break;
    }
    moment.seconds = (dt_month_days(year * 12 + month - 1) + day) * 86400;
    dt_moment_zone(&moment, value, zone, len);
    return moment;
}

/* `value` has passed dt_check_lexical for `id`, which fixes where each field sits. */
static dt_moment dt_moment_of(int id, const Py_UCS4 *value, Py_ssize_t len) {
    if (dt_is_partial(id)) {
        return dt_partial_moment_of(id, value, len);
    }
    dt_moment moment = {0, NULL, 0, 0};
    Py_ssize_t at = 0;
    if (id != DT_TIME) {
        const int negative = value[0] == '-';
        at = negative;
        /* years count as written, with 0000 between -0001 and 0001: dt_check_lexical admits it */
        const double year = dt_digit_run(value, len, &at);
        const double months = (negative ? -year : year) * 12 + fixed_digits(value, at + 1, 2, 1, 12) - 1;
        moment.seconds = (dt_month_days(months) + fixed_digits(value, at + 4, 2, 1, 31)) * 86400;
        at += id == DT_DATE ? 6 : 7; /* a dateTime goes on past its T */
    }
    if (id != DT_DATE) {
        moment.seconds += fixed_digits(value, at, 2, 0, 23) * 3600 + fixed_digits(value, at + 3, 2, 0, 59) * 60 +
                          fixed_digits(value, at + 6, 2, 0, 59);
        at += 8;
        if (at < len && value[at] == '.') {
            moment.fraction = value + ++at;
            while (at < len && is_digit(value[at])) {
                at++;
            }
            moment.fraction_len = value + at - moment.fraction;
        }
    }
    dt_moment_zone(&moment, value, at, len);
    return moment;
}

/* How `left`, moved by `shift` seconds, stands to `right`. */
static int dt_moment_cmp(const dt_moment *left, double shift, const dt_moment *right) {
    int order = dt_cmp(left->seconds + shift, right->seconds);
    const Py_ssize_t digits = left->fraction_len > right->fraction_len ? left->fraction_len : right->fraction_len;
    for (Py_ssize_t index = 0; index < digits && order == 0; index++) {
        const Py_UCS4 ours = index < left->fraction_len ? left->fraction[index] : '0';
        const Py_UCS4 theirs = index < right->fraction_len ? right->fraction[index] : '0';
        order = (ours > theirs) - (ours < theirs);
    }
    return order;
}

/* A duration as the months and the seconds it adds. The seconds' fraction stays as written, because a double cannot
   hold every decimal fraction: `fraction` points at the digits after the point, and `fraction_sign` is -1, 0 or 1 for
   a fraction that is negative, zero or positive. */
typedef struct {
    double months;
    double seconds;
    const Py_UCS4 *fraction;
    Py_ssize_t fraction_len;
    int fraction_sign;
} dt_span;

/* `value` has passed lex_duration, which takes the components in any order and any number of times. */
static dt_span dt_span_of(const Py_UCS4 *value, Py_ssize_t len) {
    const int negative = value[0] == '-';
    dt_span span = {0, 0, NULL, 0, 0};
    int in_time = 0, nonzero = 0;
    for (Py_ssize_t at = negative + 1; at < len; at++) {
        if (value[at] == 'T') {
            in_time = 1;
            continue;
        }
        const double count = dt_digit_run(value, len, &at);
        if (value[at] == '.') {
            span.fraction = value + ++at;
            while (is_digit(value[at])) {
                nonzero |= value[at] != '0';
                at++;
            }
            span.fraction_len = value + at - span.fraction;
        }
        if (in_time) {
            span.seconds += count * (value[at] == 'H' ? 3600 : (value[at] == 'M' ? 60 : 1));
        } else if (value[at] == 'D') {
            span.seconds += count * 86400;
        } else {
            span.months += count * (value[at] == 'Y' ? 12 : 1);
        }
    }
    span.fraction_sign = nonzero ? (negative ? -1 : 1) : 0;
    if (negative) {
        span.months = -span.months;
        span.seconds = -span.seconds;
    }
    return span;
}

/* How the fractions of two durations' seconds stand. The digits compare as written, padded with zeros. */
static int dt_fraction_cmp(const dt_span *left, const dt_span *right) {
    if (left->fraction_sign != right->fraction_sign) {
        return (left->fraction_sign > right->fraction_sign) - (left->fraction_sign < right->fraction_sign);
    }
    const Py_ssize_t digits = left->fraction_len > right->fraction_len ? left->fraction_len : right->fraction_len;
    int order = 0;
    for (Py_ssize_t index = 0; index < digits && order == 0; index++) {
        const Py_UCS4 ours = index < left->fraction_len ? left->fraction[index] : '0';
        const Py_UCS4 theirs = index < right->fraction_len ? right->fraction[index] : '0';
        order = (ours > theirs) - (ours < theirs);
    }
    return left->fraction_sign * order;
}

/* XSD 1.0 part 2 section 3.2.6.2: a month has no fixed length, so one duration is below another only when adding
   each to all four of these dateTimes leaves the results in that order. The four are 1696-09-01, 1697-02-01,
   1903-03-01 and 1903-07-01, as months from January of year 0. Whole seconds that tie decide by their fractions. */
static int dt_span_cmp(const dt_span *left, const dt_span *right) {
    static const double starts[] = {1696 * 12 + 8, 1697 * 12 + 1, 1903 * 12 + 2, 1903 * 12 + 6};
    int order = 0;
    for (size_t index = 0; index < sizeof(starts) / sizeof(starts[0]); index++) {
        int step = dt_cmp(dt_month_days(starts[index] + left->months) * 86400 + left->seconds,
                          dt_month_days(starts[index] + right->months) * 86400 + right->seconds);
        if (step == 0) {
            step = dt_fraction_cmp(left, right);
        }
        if (index > 0 && step != order) {
            return 2;
        }
        order = step;
    }
    return order;
}

/* A range facet's text is the schema's, not yet collapsed: its surrounding whitespace is not part of the bound. */
static void dt_trim_bound(const Py_UCS4 **bound, Py_ssize_t *bound_len) {
    while (*bound_len > 0 && is_xml_space((*bound)[*bound_len - 1])) {
        (*bound_len)--;
    }
    while (*bound_len > 0 && is_xml_space((*bound)[0])) {
        (*bound)++;
        (*bound_len)--;
    }
}

static int dt_decimal_bound(const Py_UCS4 *bound, Py_ssize_t bound_len, dt_decimal *digits) {
    dt_trim_bound(&bound, &bound_len);
    if (!dt_check_lexical(DT_DECIMAL, bound, bound_len)) {
        return 0;
    }
    *digits = dt_decimal_of(bound, bound_len);
    return 1;
}

/* How `value`, which has passed dt_check_lexical for the dt_is_ordered type `id`, stands to a range facet's bound:
   -1 below, 0 equal, 1 above. 2 says that the type's order relation does not place the two -- a month against a
   count of days, a value with a timezone within fourteen hours of one without (XSD 1.0 part 2 sections 3.2.6.2 and
   3.2.7.4) -- or that the bound is no value of the type, and then no range facet holds. */
static int dt_order(int id, const Py_UCS4 *value, Py_ssize_t len, const Py_UCS4 *bound, Py_ssize_t bound_len) {
    dt_trim_bound(&bound, &bound_len);
    if (!dt_check_lexical(id, bound, bound_len)) {
        return 2;
    }
    if (id == DT_DURATION) {
        const dt_span left = dt_span_of(value, len), right = dt_span_of(bound, bound_len);
        return dt_span_cmp(&left, &right);
    }
    const dt_moment left = dt_moment_of(id, value, len), right = dt_moment_of(id, bound, bound_len);
    if (left.zoned == right.zoned) {
        return dt_moment_cmp(&left, 0, &right);
    }
    /* the one without a timezone can lie anywhere from +14:00 to -14:00, and the order has to hold at both ends */
    if (dt_moment_cmp(&left, 14 * 3600, &right) == -1) {
        return -1;
    }
    return dt_moment_cmp(&left, -14 * 3600, &right) == 1 ? 1 : 2;
}

/* ---- facets ---- */

typedef struct {
    const Py_UCS4 *ptr;
    Py_ssize_t len;
} strspan;

enum { RANGE_MIN_INCLUSIVE, RANGE_MAX_INCLUSIVE, RANGE_MIN_EXCLUSIVE, RANGE_MAX_EXCLUSIVE, RANGE_COUNT };

/* A range facet's bound as the schema wrote it. A decimal or integer type compares against `digits`, read once when the
   schema compiles; `valid` is 0 when the bound is not a decimal literal, and such a bound admits no value. */
typedef struct {
    strspan text;
    int valid;
    dt_decimal digits;
} facet_range;

typedef struct {
    Py_ssize_t length, min_length, max_length, total_digits, fraction_digits;
} facet_limits;

typedef struct {
    int base_id;
    int ws;
    int has_length, has_min_length, has_max_length;
    int has_min_inclusive, has_max_inclusive, has_min_exclusive, has_max_exclusive;
    double min_inclusive, max_inclusive, min_exclusive, max_exclusive;
    /* the bounds as the schema wrote them, by RANGE_ slot: a date's, time's or duration's order is not a double's.
       Held in the arena so the facetset that every simple value zeroes stays small. */
    facet_range *ranges;
    facet_limits *limits;
    int has_total_digits, has_fraction_digits;
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

static int facet_range_text(arena *mem, facetset *facets, int slot, const Py_UCS4 *value, Py_ssize_t value_len) {
    if (facets->ranges == NULL) {
        facets->ranges = arena_alloc(mem, RANGE_COUNT * sizeof(facet_range));
        if (facets->ranges == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return -1;                /* GCOVR_EXCL_LINE */
        }
    }
    facet_range *range = &facets->ranges[slot];
    range->text = (strspan){value, value_len};
    range->valid = dt_decimal_bound(value, value_len, &range->digits);
    return 0;
}

/* A length or digit-count limit, read as the integer it starts with. A limit past Py_ssize_t saturates: no value has
   that many characters or digits, so the verdict is unchanged. */
static Py_ssize_t facet_limit(const Py_UCS4 *value, Py_ssize_t len) {
    Py_ssize_t index = 0;
    while (index < len && is_xml_space(value[index])) {
        index++;
    }
    const int negative = index < len && value[index] == '-';
    if (index < len && (value[index] == '+' || value[index] == '-')) {
        index++;
    }
    Py_ssize_t limit = 0;
    while (index < len && is_digit(value[index])) {
        const Py_ssize_t digit = value[index++] - '0';
        limit = limit > (PY_SSIZE_T_MAX - digit) / 10 ? PY_SSIZE_T_MAX : limit * 10 + digit;
    }
    return negative ? -limit : limit;
}

/* The length and digit-count limits of a facetset, allocated on the first of them it takes. */
static int facet_limits_at(arena *mem, facetset *facets) {
    if (facets->limits == NULL) {
        facets->limits = arena_alloc(mem, sizeof(facet_limits));
        if (facets->limits == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return -1;                /* GCOVR_EXCL_LINE */
        }
    }
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
        if (facet_limits_at(mem, facets) < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return -1;                          /* GCOVR_EXCL_LINE */
        }
        facets->has_length = 1;
        facets->limits->length = facet_limit(value, value_len);
    } else if (u_eq_ascii(name, name_len, "minLength")) {
        if (facet_limits_at(mem, facets) < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return -1;                          /* GCOVR_EXCL_LINE */
        }
        facets->has_min_length = 1;
        facets->limits->min_length = facet_limit(value, value_len);
    } else if (u_eq_ascii(name, name_len, "maxLength")) {
        if (facet_limits_at(mem, facets) < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return -1;                          /* GCOVR_EXCL_LINE */
        }
        facets->has_max_length = 1;
        facets->limits->max_length = facet_limit(value, value_len);
    } else if (u_eq_ascii(name, name_len, "minInclusive")) {
        if (facet_range_text(mem, facets, RANGE_MIN_INCLUSIVE, value, value_len) < 0) { /* GCOVR_EXCL_BR_LINE */
            return -1;                                                                  /* GCOVR_EXCL_LINE */
        }
        facets->has_min_inclusive = 1;
        facets->min_inclusive = dt_to_double(mem, value, value_len);
    } else if (u_eq_ascii(name, name_len, "maxInclusive")) {
        if (facet_range_text(mem, facets, RANGE_MAX_INCLUSIVE, value, value_len) < 0) { /* GCOVR_EXCL_BR_LINE */
            return -1;                                                                  /* GCOVR_EXCL_LINE */
        }
        facets->has_max_inclusive = 1;
        facets->max_inclusive = dt_to_double(mem, value, value_len);
    } else if (u_eq_ascii(name, name_len, "minExclusive")) {
        if (facet_range_text(mem, facets, RANGE_MIN_EXCLUSIVE, value, value_len) < 0) { /* GCOVR_EXCL_BR_LINE */
            return -1;                                                                  /* GCOVR_EXCL_LINE */
        }
        facets->has_min_exclusive = 1;
        facets->min_exclusive = dt_to_double(mem, value, value_len);
    } else if (u_eq_ascii(name, name_len, "maxExclusive")) {
        if (facet_range_text(mem, facets, RANGE_MAX_EXCLUSIVE, value, value_len) < 0) { /* GCOVR_EXCL_BR_LINE */
            return -1;                                                                  /* GCOVR_EXCL_LINE */
        }
        facets->has_max_exclusive = 1;
        facets->max_exclusive = dt_to_double(mem, value, value_len);
    } else if (u_eq_ascii(name, name_len, "totalDigits")) {
        if (facet_limits_at(mem, facets) < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return -1;                          /* GCOVR_EXCL_LINE */
        }
        facets->has_total_digits = 1;
        facets->limits->total_digits = facet_limit(value, value_len);
    } else if (u_eq_ascii(name, name_len, "fractionDigits")) {
        if (facet_limits_at(mem, facets) < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return -1;                          /* GCOVR_EXCL_LINE */
        }
        facets->has_fraction_digits = 1;
        facets->limits->fraction_digits = facet_limit(value, value_len);
    }
    return 0;
}

/* Fold the scalar facets and whiteSpace rule `own` states into `facets`, replacing what an earlier derivation stated.
   `own->ws` is -1 when it states no whiteSpace. */
static int facet_inherit(arena *mem, facetset *facets, const facetset *own) {
    if (own->ranges != NULL && facets->ranges == NULL) {
        facets->ranges = arena_alloc(mem, RANGE_COUNT * sizeof(facet_range));
        if (facets->ranges == NULL) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return -1;                /* GCOVR_EXCL_LINE */
        }
    }
    if (own->ws >= 0 && !dt_is_list(facets->base_id)) {
        facets->ws = own->ws;
    }
    if (own->has_length) {
        if (facet_limits_at(mem, facets) < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return -1;                          /* GCOVR_EXCL_LINE */
        }
        facets->has_length = 1;
        facets->limits->length = own->limits->length;
    }
    if (own->has_min_length) {
        if (facet_limits_at(mem, facets) < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return -1;                          /* GCOVR_EXCL_LINE */
        }
        facets->has_min_length = 1;
        facets->limits->min_length = own->limits->min_length;
    }
    if (own->has_max_length) {
        if (facet_limits_at(mem, facets) < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return -1;                          /* GCOVR_EXCL_LINE */
        }
        facets->has_max_length = 1;
        facets->limits->max_length = own->limits->max_length;
    }
    if (own->has_min_inclusive) {
        facets->has_min_inclusive = 1;
        facets->min_inclusive = own->min_inclusive;
        facets->ranges[RANGE_MIN_INCLUSIVE] = own->ranges[RANGE_MIN_INCLUSIVE];
    }
    if (own->has_max_inclusive) {
        facets->has_max_inclusive = 1;
        facets->max_inclusive = own->max_inclusive;
        facets->ranges[RANGE_MAX_INCLUSIVE] = own->ranges[RANGE_MAX_INCLUSIVE];
    }
    if (own->has_min_exclusive) {
        facets->has_min_exclusive = 1;
        facets->min_exclusive = own->min_exclusive;
        facets->ranges[RANGE_MIN_EXCLUSIVE] = own->ranges[RANGE_MIN_EXCLUSIVE];
    }
    if (own->has_max_exclusive) {
        facets->has_max_exclusive = 1;
        facets->max_exclusive = own->max_exclusive;
        facets->ranges[RANGE_MAX_EXCLUSIVE] = own->ranges[RANGE_MAX_EXCLUSIVE];
    }
    if (own->has_total_digits) {
        if (facet_limits_at(mem, facets) < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return -1;                          /* GCOVR_EXCL_LINE */
        }
        facets->has_total_digits = 1;
        facets->limits->total_digits = own->limits->total_digits;
    }
    if (own->has_fraction_digits) {
        if (facet_limits_at(mem, facets) < 0) { /* GCOVR_EXCL_BR_LINE: arena OOM is unforceable */
            return -1;                          /* GCOVR_EXCL_LINE */
        }
        facets->has_fraction_digits = 1;
        facets->limits->fraction_digits = own->limits->fraction_digits;
    }
    return 0;
}

/* Whether a value of a dt_is_ordered type keeps one range facet: `side` is 1 for a lower bound and -1 for an upper
   one. A value the order relation does not place against the bound keeps none. */
static int facet_bound_ok(int id, const facetset *facets, int slot, const Py_UCS4 *value, Py_ssize_t len,
                          const dt_decimal *number, int side, int inclusive) {
    const facet_range *range = &facets->ranges[slot];
    int order;
    if (dt_is_decimal_family(id)) {
        order = range->valid ? dt_decimal_compare(number, &range->digits) : 2;
    } else {
        order = dt_order(id, value, len, range->text.ptr, range->text.len);
    }
    return order == side || (inclusive && order == 0);
}

static void count_digits(const Py_UCS4 *value, Py_ssize_t len, Py_ssize_t *total, Py_ssize_t *fraction) {
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

/* The derivations of an XSD type's chain whose enumerations or patterns apply, for a kind stated on more than one of
   them; a kind stated on one derivation is read through that facetset's own array. */
typedef struct {
    const facetset *const *enums;
    const facetset *const *patterns;
    Py_ssize_t enum_count, pattern_count;
} facet_levels;

/* The patterns of one restriction step are alternatives, as if they were branches of one regular expression (XSD Part 2
   4.3.4.3), so the value matches the step when any of them does. Each step must still match on its own. */
static int pattern_step_matches(th_schema *schema, const strspan *patterns, Py_ssize_t count, const Py_UCS4 *value,
                                Py_ssize_t len) {
    for (Py_ssize_t index = 0; index < count; index++) {
        if (regex_full_match(schema, patterns[index].ptr, patterns[index].len, value, len)) {
            return 1;
        }
    }
    return 0;
}

/* Apply every facet to the whitespace-normalized value, reporting each violation on `node`: the facets of `facets`,
   then the enumerations and patterns of `levels`. `units` is what the length facets measure: the characters of an
   atomic value, the items of a list. Returns 1 when all pass. */
static int facet_check(valctx *ctx, th_node *node, const facetset *facets, const facet_levels *levels,
                       const Py_UCS4 *value, Py_ssize_t len, Py_ssize_t units) {
    int ok = 1;
    if (facets->has_length && units != facets->limits->length) {
        report(ctx, node, "facet", "value length %zd does not equal length facet %zd", units, facets->limits->length);
        ok = 0;
    }
    if (facets->has_min_length && units < facets->limits->min_length) {
        report(ctx, node, "facet", "value length %zd is below minLength %zd", units, facets->limits->min_length);
        ok = 0;
    }
    if (facets->has_max_length && units > facets->limits->max_length) {
        report(ctx, node, "facet", "value length %zd exceeds maxLength %zd", units, facets->limits->max_length);
        ok = 0;
    }
    if (dt_is_float(facets->base_id) && (facets->has_min_inclusive || facets->has_max_inclusive ||
                                         facets->has_min_exclusive || facets->has_max_exclusive)) {
        double number = dt_value_double(ctx->schema, value, len);
        if (facets->has_min_inclusive && !dt_double_bound_ok(number, facets->min_inclusive, 1, 1)) {
            report(ctx, node, "facet", "value is below minInclusive");
            ok = 0;
        }
        if (facets->has_max_inclusive && !dt_double_bound_ok(number, facets->max_inclusive, -1, 1)) {
            report(ctx, node, "facet", "value is above maxInclusive");
            ok = 0;
        }
        if (facets->has_min_exclusive && !dt_double_bound_ok(number, facets->min_exclusive, 1, 0)) {
            report(ctx, node, "facet", "value is not above minExclusive");
            ok = 0;
        }
        if (facets->has_max_exclusive && !dt_double_bound_ok(number, facets->max_exclusive, -1, 0)) {
            report(ctx, node, "facet", "value is not below maxExclusive");
            ok = 0;
        }
    } else if (dt_is_ordered(facets->base_id) && (facets->has_min_inclusive || facets->has_max_inclusive ||
                                                  facets->has_min_exclusive || facets->has_max_exclusive)) {
        const int id = facets->base_id;
        const dt_decimal number = dt_is_decimal_family(id) ? dt_decimal_of(value, len) : (dt_decimal){0};
        if (facets->has_min_inclusive && !facet_bound_ok(id, facets, RANGE_MIN_INCLUSIVE, value, len, &number, 1, 1)) {
            report(ctx, node, "facet", "value is below minInclusive");
            ok = 0;
        }
        if (facets->has_max_inclusive && !facet_bound_ok(id, facets, RANGE_MAX_INCLUSIVE, value, len, &number, -1, 1)) {
            report(ctx, node, "facet", "value is above maxInclusive");
            ok = 0;
        }
        if (facets->has_min_exclusive && !facet_bound_ok(id, facets, RANGE_MIN_EXCLUSIVE, value, len, &number, 1, 0)) {
            report(ctx, node, "facet", "value is not above minExclusive");
            ok = 0;
        }
        if (facets->has_max_exclusive && !facet_bound_ok(id, facets, RANGE_MAX_EXCLUSIVE, value, len, &number, -1, 0)) {
            report(ctx, node, "facet", "value is not below maxExclusive");
            ok = 0;
        }
    }
    if (facets->has_total_digits || facets->has_fraction_digits) {
        Py_ssize_t total, fraction;
        count_digits(value, len, &total, &fraction);
        if (facets->has_total_digits && total > facets->limits->total_digits) {
            report(ctx, node, "facet", "value has more than totalDigits %zd significant digits",
                   facets->limits->total_digits);
            ok = 0;
        }
        if (facets->has_fraction_digits && fraction > facets->limits->fraction_digits) {
            report(ctx, node, "facet", "value has more than fractionDigits %zd fraction digits",
                   facets->limits->fraction_digits);
            ok = 0;
        }
    }
    if (facets->enum_count > 0 || levels->enum_count > 0) {
        int matched = 0;
        for (Py_ssize_t index = 0; index < facets->enum_count && !matched; index++) {
            if (u_eq_u(value, len, facets->enums[index].ptr, facets->enums[index].len)) {
                matched = 1;
            }
        }
        for (Py_ssize_t level = 0; level < levels->enum_count && !matched; level++) {
            TH_WORK_STEP();
            const facetset *set = levels->enums[level];
            for (Py_ssize_t index = 0; index < set->enum_count && !matched; index++) {
                matched = u_eq_u(value, len, set->enums[index].ptr, set->enums[index].len);
            }
        }
        if (!matched) {
            report(ctx, node, "facet", "value is not one of the enumerated values");
            ok = 0;
        }
    }
    if (facets->pattern_count > 0 &&
        !pattern_step_matches(ctx->schema, facets->patterns, facets->pattern_count, value, len)) {
        report(ctx, node, "facet", "value does not match the required pattern");
        ok = 0;
    }
    for (Py_ssize_t level = 0; level < levels->pattern_count; level++) {
        TH_WORK_STEP();
        const facetset *set = levels->patterns[level];
        if (!pattern_step_matches(ctx->schema, set->patterns, set->pattern_count, value, len)) {
            report(ctx, node, "facet", "value does not match the required pattern");
            ok = 0;
        }
    }
    return ok;
}

#endif /* TURBOHTML_VALIDATE_DATATYPES_H */
