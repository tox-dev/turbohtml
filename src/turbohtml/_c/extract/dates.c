/* Publication-date extraction for turbohtml.extract.dates, the htmldate.find_date counterpart.

   The shim parses the page, computes the [min, max] window and hands the tree here; Document._dates runs the
   signals in htmldate's priority order -- a date in the canonical URL, then publication/modification <meta> tags,
   then JSON-LD, then <time> elements and date-marked elements, then (as a last resort) the visible text -- and the
   first stage that yields a date inside the window wins. Within a stage the wanted role (publication for
   original=True, modification otherwise) or a generic date wins on sight and the first off-role date is the reserve.

   The date-string parser the stages share is also exposed on its own, as the conformance surface for its grammar:
     _date_scan(text, year)     -> the first numeric date (ISO 8601, an 8-digit stamp, or a day-month-year
                                   spelling), or None, trying the patterns in that order.
     _date_scan_all(text, year) -> every ISO, day-month-year, and written-out date, in that pattern order, the
                                   sweep the text stage's frequency scoring reads.
     _date_url(url)             -> the /YYYY/MM/DD/ date a URL path carries.
   `year` is the current year, the pivot a two-digit year expands against. Each returns (year, month, day) int
   tuples; a calendar-impossible combination (Feb 30, a non-leap Feb 29) is rejected the way date() raises.

   Two deliberate narrowings from the regexes the grammar was written from, both outside any realistic date string:
   the digit guards and the day-month-year year run read ASCII [0-9] rather than Unicode \d, and the whitespace
   between written-out date tokens is ASCII [ \t\n\r\f\v] rather than Unicode \s. */

#include "core/ascii.h"
#include "core/common.h"

#include "dom/nodes.h"
#include "tokenizer/binding.h" /* Py_BEGIN_CRITICAL_SECTION shim for the GIL/pre-3.13 build */

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#define CP(index) PyUnicode_READ(kind, data, (index))
#define COUNT_OF(table) (sizeof(table) / sizeof(*(table)))

/* Lowercase for the case-insensitive month vocabulary: the ASCII fold plus the
   Latin-1 uppercase block (0xC0-0xDE minus the 0xD7 multiplication sign), which
   covers every accent the month names carry (É, Ä, Û). This is wider than the
   shared ASCII lower_ascii on purpose; nothing else in the date grammar needs
   folding. */
static inline Py_UCS4 lower_month_cp(Py_UCS4 codepoint) {
    if (codepoint >= 'A' && codepoint <= 'Z') {
        return codepoint + ('a' - 'A');
    }
    if (codepoint >= 0xC0 && codepoint <= 0xDE && codepoint != 0xD7) {
        return codepoint + 0x20;
    }
    return codepoint;
}

/* The Perl \s class -- SPACE, TAB, LF, VT, FF, CR -- the \s+ between written-out
   date tokens. This is the whitespace the ported _dates.py regex matched, so it
   keeps the vertical tab (0x0B) that HTML "ASCII whitespace" (the shared is_space)
   omits; a date string is arbitrary extracted text, not a tokenizer stream, and a
   stray VT between tokens still separates them. */
static inline int is_perl_space(Py_UCS4 codepoint) {
    return codepoint == ' ' || (codepoint >= 0x09 && codepoint <= 0x0D);
}

/* A valid four-digit year, _YEAR = 199[0-9]|20[0-9]{2}: 1990-1999 or 2000-2099. */
static int year_at(const void *data, int kind, Py_ssize_t len, Py_ssize_t pos, int *out_year) {
    if (pos + 4 > len) {
        return 0;
    }
    Py_UCS4 a = CP(pos), b = CP(pos + 1), c = CP(pos + 2), d = CP(pos + 3);
    if (!(is_ascii_digit(a) && is_ascii_digit(b) && is_ascii_digit(c) && is_ascii_digit(d))) {
        return 0;
    }
    if (!((a == '1' && b == '9' && c == '9') || (a == '2' && b == '0'))) {
        return 0;
    }
    *out_year = (int)((a - '0') * 1000 + (b - '0') * 100 + (c - '0') * 10 + (d - '0'));
    return 1;
}

/* _MONTH = 1[0-2]|0[1-9]|[1-9], split by width. The two-digit form needs a
   leading 0 or 1; the one-digit form is 1-9. */
static int month2(Py_UCS4 d0, Py_UCS4 d1, int *out) {
    if (d0 == '1' && d1 >= '0' && d1 <= '2') {
        *out = 10 + (int)(d1 - '0');
        return 1;
    }
    if (d0 == '0' && d1 >= '1' && d1 <= '9') {
        *out = (int)(d1 - '0');
        return 1;
    }
    return 0;
}

static int month1(Py_UCS4 d0, int *out) {
    if (d0 >= '1' && d0 <= '9') {
        *out = (int)(d0 - '0');
        return 1;
    }
    return 0;
}

/* _DAY = 3[01]|[12][0-9]|0[1-9]|[1-9], split by width. */
static int day2(Py_UCS4 d0, Py_UCS4 d1, int *out) {
    if (d0 == '3' && (d1 == '0' || d1 == '1')) {
        *out = 30 + (int)(d1 - '0');
        return 1;
    }
    if ((d0 == '1' || d0 == '2') && d1 >= '0' && d1 <= '9') {
        *out = (int)(d0 - '0') * 10 + (int)(d1 - '0');
        return 1;
    }
    if (d0 == '0' && d1 >= '1' && d1 <= '9') {
        *out = (int)(d1 - '0');
        return 1;
    }
    return 0;
}

static int day1(Py_UCS4 d0, int *out) {
    if (d0 >= '1' && d0 <= '9') {
        *out = (int)(d0 - '0');
        return 1;
    }
    return 0;
}

static int is_leap(int year) {
    return (year % 4 == 0 && year % 100 != 0) || (year % 400 == 0);
}

/* A calendar-valid (month, day), the check date() runs when the shim builds the
   result. The year is always in range here, so only the month/day are gated. */
static int ymd_valid(int year, int month, int day) {
    static const int DAYS[] = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
    if (month < 1 || month > 12) {
        return 0;
    }
    int limit = DAYS[month - 1];
    if (month == 2 && is_leap(year)) {
        limit = 29;
    }
    return day >= 1 && day <= limit;
}

/* An ISO date, _ISO_DATE = (YEAR)[-/.](MONTH)[-/.](DAY), starting exactly at pos.
   The two separators are chosen independently. *out_end is the position after the
   match. The month/day are greedy (two digits before one); a two-digit month that
   is not followed by a separator falls back to one digit, but never the reverse,
   since the char after a two-digit month is a digit and a one-digit month needs a
   separator there. */
static int iso_at(const void *data, int kind, Py_ssize_t len, Py_ssize_t pos, int *year, int *month, int *day,
                  Py_ssize_t *out_end) {
    if (!year_at(data, kind, len, pos, year)) {
        return 0;
    }
    Py_ssize_t cursor = pos + 4;
    if (cursor >= len || !(CP(cursor) == '-' || CP(cursor) == '/' || CP(cursor) == '.')) {
        return 0;
    }
    cursor++;
    if (cursor + 2 < len && month2(CP(cursor), CP(cursor + 1), month) &&
        (CP(cursor + 2) == '-' || CP(cursor + 2) == '/' || CP(cursor + 2) == '.')) {
        cursor += 2;
    } else if (cursor + 1 < len && month1(CP(cursor), month) &&
               (CP(cursor + 1) == '-' || CP(cursor + 1) == '/' || CP(cursor + 1) == '.')) {
        cursor += 1;
    } else {
        return 0;
    }
    cursor++;
    if (cursor + 1 < len && day2(CP(cursor), CP(cursor + 1), day)) {
        cursor += 2;
    } else if (cursor < len && day1(CP(cursor), day)) {
        cursor += 1;
    } else {
        return 0;
    }
    *out_end = cursor;
    return 1;
}

/* A compact stamp, _COMPACT_DATE = (?<!\d)(YEAR)(MONTH)(DAY)(?!\d), whose digit
   run at pos must be exactly a year (four digits) plus a month plus a day. Since
   the run is maximal (the boundary guards) the whole of it is consumed, so its
   length fixes the month/day widths: 8 = two + two, 7 = two + one or one + two
   (greedy prefers the wider month), 6 = one + one. The caller guarantees the
   (?<!\d) boundary. */
static int compact_at(const void *data, int kind, Py_ssize_t len, Py_ssize_t pos, int *year, int *month, int *day) {
    Py_ssize_t run = pos;
    while (run < len && is_ascii_digit(CP(run))) {
        run++;
    }
    Py_ssize_t width = run - pos;
    if (width < 6 || width > 8) {
        return 0;
    }
    if (!year_at(data, kind, len, pos, year)) {
        return 0;
    }
    Py_ssize_t rest = pos + 4;
    if (width == 8) {
        return month2(CP(rest), CP(rest + 1), month) && day2(CP(rest + 2), CP(rest + 3), day);
    }
    if (width == 7) {
        if (month2(CP(rest), CP(rest + 1), month) && day1(CP(rest + 2), day)) {
            return 1;
        }
        return month1(CP(rest), month) && day2(CP(rest + 1), CP(rest + 2), day);
    }
    return month1(CP(rest), month) && day1(CP(rest + 1), day);
}

/* A day-first spelling, _DMY_DATE = (?<!\d)([0-3]?[0-9])[-/.]([0-1]?[0-9])[-/.](\d{2,4})(?!\d),
   starting at pos (the caller guarantees the (?<!\d) boundary). The raw day and
   month groups are returned unswapped; the caller applies _swap and _correct_year.
   The year run is two to four ASCII digits ended by a non-digit. */
static int dmy_at(const void *data, int kind, Py_ssize_t len, Py_ssize_t pos, int *raw_day, int *raw_month,
                  int *raw_year, Py_ssize_t *out_end) {
    Py_ssize_t cursor = pos;
    if (cursor + 2 < len && CP(cursor) >= '0' && CP(cursor) <= '3' && is_ascii_digit(CP(cursor + 1)) &&
        (CP(cursor + 2) == '-' || CP(cursor + 2) == '/' || CP(cursor + 2) == '.')) {
        *raw_day = (int)(CP(cursor) - '0') * 10 + (int)(CP(cursor + 1) - '0');
        cursor += 2;
    } else if (cursor + 1 < len && is_ascii_digit(CP(cursor)) &&
               (CP(cursor + 1) == '-' || CP(cursor + 1) == '/' || CP(cursor + 1) == '.')) {
        *raw_day = (int)(CP(cursor) - '0');
        cursor += 1;
    } else {
        return 0;
    }
    cursor++;
    if (cursor + 2 < len && CP(cursor) >= '0' && CP(cursor) <= '1' && is_ascii_digit(CP(cursor + 1)) &&
        (CP(cursor + 2) == '-' || CP(cursor + 2) == '/' || CP(cursor + 2) == '.')) {
        *raw_month = (int)(CP(cursor) - '0') * 10 + (int)(CP(cursor + 1) - '0');
        cursor += 2;
    } else if (cursor + 1 < len && is_ascii_digit(CP(cursor)) &&
               (CP(cursor + 1) == '-' || CP(cursor + 1) == '/' || CP(cursor + 1) == '.')) {
        *raw_month = (int)(CP(cursor) - '0');
        cursor += 1;
    } else {
        return 0;
    }
    cursor++;
    Py_ssize_t start = cursor;
    while (cursor < len && is_ascii_digit(CP(cursor))) {
        cursor++;
    }
    Py_ssize_t run = cursor - start;
    if (run < 2 || run > 4) {
        return 0;
    }
    int value = 0;
    for (Py_ssize_t index = start; index < cursor; index++) {
        value = value * 10 + (int)(CP(index) - '0');
    }
    *raw_year = value;
    *out_end = cursor;
    return 1;
}

/* _correct_year: a two-digit year expands to the recent century, this year's
   two-digit value winning ties. */
static int correct_year(int year, int current_year) {
    if (year < 100) {
        return year + (year <= current_year % 100 ? 2000 : 1900);
    }
    return year;
}

/* _swap then build: _ymd(year, *_swap(day, month)). A month field over 12 that
   cannot be a month is read as the day, swapping the pair. Returns 1 with a
   calendar-valid (month, day) in *out_*, 0 otherwise. */
static int dmy_resolve(int raw_day, int raw_month, int year, int *out_month, int *out_day) {
    if (raw_month > 12 && raw_day <= 12) {
        *out_month = raw_day;
        *out_day = raw_month;
    } else {
        *out_month = raw_month;
        *out_day = raw_day;
    }
    return ymd_valid(year, *out_month, *out_day);
}

/* The month vocabulary (English, German, French, Spanish, Italian), the compact
   htmldate set the visible-text stage reads. Stored lowercase as UTF-8 with the
   code-point length alongside, since a few names carry a Latin-1 accent (février,
   märz, août, décembre). The regex alternates the names longest-first, so a match
   takes the longest name present; two names that both match a position agree on
   their shared prefix, so the longest is unambiguous. */
typedef struct {
    const char *name;
    uint8_t length;
    uint8_t month;
} month_name;

static const month_name MONTH_NAMES[] = {
    {"jan", 3, 1},       {"januar", 6, 1},      {"january", 7, 1},    {"janvier", 7, 1},   {"enero", 5, 1},
    {"gennaio", 7, 1},   {"feb", 3, 2},         {"februar", 7, 2},    {"february", 8, 2},  {"février", 7, 2},
    {"febrero", 7, 2},   {"febbraio", 8, 2},    {"mar", 3, 3},        {"märz", 4, 3},      {"march", 5, 3},
    {"mars", 4, 3},      {"marzo", 5, 3},       {"apr", 3, 4},        {"april", 5, 4},     {"avril", 5, 4},
    {"abril", 5, 4},     {"aprile", 6, 4},      {"may", 3, 5},        {"mai", 3, 5},       {"mayo", 4, 5},
    {"maggio", 6, 5},    {"jun", 3, 6},         {"juni", 4, 6},       {"june", 4, 6},      {"juin", 4, 6},
    {"junio", 5, 6},     {"giugno", 6, 6},      {"jul", 3, 7},        {"juli", 4, 7},      {"july", 4, 7},
    {"juillet", 7, 7},   {"julio", 5, 7},       {"luglio", 6, 7},     {"aug", 3, 8},       {"august", 6, 8},
    {"aout", 4, 8},      {"août", 4, 8},        {"agosto", 6, 8},     {"sep", 3, 9},       {"september", 9, 9},
    {"septembre", 9, 9}, {"septiembre", 10, 9}, {"settembre", 9, 9},  {"oct", 3, 10},      {"oktober", 7, 10},
    {"october", 7, 10},  {"octobre", 7, 10},    {"octubre", 7, 10},   {"ottobre", 7, 10},  {"nov", 3, 11},
    {"november", 8, 11}, {"novembre", 8, 11},   {"noviembre", 9, 11}, {"dec", 3, 12},      {"dezember", 8, 12},
    {"december", 8, 12}, {"décembre", 8, 12},   {"diciembre", 9, 12}, {"dicembre", 8, 12},
};

/* Decode the next code point of a stored month name (ASCII, or a two-byte Latin-1
   accent), advancing *cursor. The names hold no other byte widths. */
static Py_UCS4 name_cp(const char **cursor) {
    unsigned char lead = (unsigned char)**cursor;
    (*cursor)++;
    if (lead < 0x80) {
        return lead;
    }
    unsigned char trail = (unsigned char)**cursor;
    (*cursor)++;
    return (Py_UCS4)(((lead & 0x1F) << 6) | (trail & 0x3F));
}

/* The longest month name in the vocabulary that matches at pos (case-insensitive),
   the token the alternation captures. Returns its code-point length (0 for none)
   and writes the month to *out_month. */
static int match_month_name(const void *data, int kind, Py_ssize_t len, Py_ssize_t pos, int *out_month) {
    int best_length = 0;
    for (size_t index = 0; index < sizeof(MONTH_NAMES) / sizeof(MONTH_NAMES[0]); index++) {
        const month_name *entry = &MONTH_NAMES[index];
        if (pos + entry->length > len || entry->length <= best_length) {
            continue;
        }
        const char *cursor = entry->name;
        int matched = 1;
        for (uint8_t offset = 0; offset < entry->length; offset++) {
            if (lower_month_cp(CP(pos + offset)) != name_cp(&cursor)) {
                matched = 0;
                break;
            }
        }
        if (matched) {
            best_length = entry->length;
            *out_month = entry->month;
        }
    }
    return best_length;
}

/* An ordinal suffix, (?:st|nd|rd|th)?, matched case-insensitively; returns its
   width (2) or 0. */
static int match_ordinal(const void *data, int kind, Py_ssize_t len, Py_ssize_t pos) {
    if (pos + 2 > len) {
        return 0;
    }
    Py_UCS4 a = lower_month_cp(CP(pos)), b = lower_month_cp(CP(pos + 1));
    if ((a == 's' && b == 't') || (a == 'n' && b == 'd') || (a == 'r' && b == 'd') || (a == 't' && b == 'h')) {
        return 2;
    }
    return 0;
}

/* Skip a run of ASCII whitespace, requiring at least one (\s+). Returns the
   position after it, or -1 when none is present. */
static Py_ssize_t skip_spaces(const void *data, int kind, Py_ssize_t len, Py_ssize_t pos) {
    if (pos >= len || !is_perl_space(CP(pos))) {
        return -1;
    }
    while (pos < len && is_perl_space(CP(pos))) {
        pos++;
    }
    return pos;
}

/* The tail of the month-first spelling after the day, (?:st|nd|rd|th)?,?\s+(YEAR).
   The ordinal is tried present first, then absent, the only backtrack the tail
   needs. Returns 1 with the year and end position. */
static int text_a_tail(const void *data, int kind, Py_ssize_t len, Py_ssize_t start, int *year, Py_ssize_t *out_end) {
    for (int with_ordinal = 1; with_ordinal >= 0; with_ordinal--) {
        Py_ssize_t cursor = start;
        if (with_ordinal) {
            int ordinal = match_ordinal(data, kind, len, cursor);
            if (ordinal == 0) {
                continue;
            }
            cursor += ordinal;
        }
        if (cursor < len && CP(cursor) == ',') {
            cursor++;
        }
        cursor = skip_spaces(data, kind, len, cursor);
        if (cursor < 0) {
            continue;
        }
        if (year_at(data, kind, len, cursor, year)) {
            *out_end = cursor + 4;
            return 1;
        }
    }
    return 0;
}

/* The month-first branch, (MONTH)\.?\s+([0-3]?[0-9])(?:...)?,?\s+(YEAR), at pos.
   The day is greedy (two digits before one). Returns 1 with month/day/year and
   the end position. */
static int text_a(const void *data, int kind, Py_ssize_t len, Py_ssize_t pos, int *month, int *day, int *year,
                  Py_ssize_t *out_end) {
    int name_length = match_month_name(data, kind, len, pos, month);
    if (name_length == 0) {
        return 0;
    }
    Py_ssize_t cursor = pos + name_length;
    if (cursor < len && CP(cursor) == '.') {
        cursor++;
    }
    cursor = skip_spaces(data, kind, len, cursor);
    if (cursor < 0) {
        return 0;
    }
    if (cursor + 1 < len && CP(cursor) >= '0' && CP(cursor) <= '3' && is_ascii_digit(CP(cursor + 1)) &&
        text_a_tail(data, kind, len, cursor + 2, year, out_end)) {
        *day = (int)(CP(cursor) - '0') * 10 + (int)(CP(cursor + 1) - '0');
        return 1;
    }
    if (cursor < len && is_ascii_digit(CP(cursor)) && text_a_tail(data, kind, len, cursor + 1, year, out_end)) {
        *day = (int)(CP(cursor) - '0');
        return 1;
    }
    return 0;
}

/* The tail of the day-first spelling after the day,
   (?:st|nd|rd|th)?\.?\s+(?:of\s+)?(MONTH)\.?,?\s+(YEAR). The ordinal is tried
   present first, then absent. Returns 1 with month/year and the end position. */
static int text_b_tail(const void *data, int kind, Py_ssize_t len, Py_ssize_t start, int *month, int *year,
                       Py_ssize_t *out_end) {
    for (int with_ordinal = 1; with_ordinal >= 0; with_ordinal--) {
        Py_ssize_t cursor = start;
        if (with_ordinal) {
            int ordinal = match_ordinal(data, kind, len, cursor);
            if (ordinal == 0) {
                continue;
            }
            cursor += ordinal;
        }
        if (cursor < len && CP(cursor) == '.') {
            cursor++;
        }
        cursor = skip_spaces(data, kind, len, cursor);
        if (cursor < 0) {
            continue;
        }
        if (cursor + 2 < len && lower_month_cp(CP(cursor)) == 'o' && lower_month_cp(CP(cursor + 1)) == 'f' &&
            is_perl_space(CP(cursor + 2))) {
            cursor = skip_spaces(data, kind, len, cursor + 2);
        }
        int name_length = match_month_name(data, kind, len, cursor, month);
        if (name_length == 0) {
            continue;
        }
        Py_ssize_t scan_end = cursor + name_length;
        if (scan_end < len && CP(scan_end) == '.') {
            scan_end++;
        }
        if (scan_end < len && CP(scan_end) == ',') {
            scan_end++;
        }
        scan_end = skip_spaces(data, kind, len, scan_end);
        if (scan_end < 0) {
            continue;
        }
        if (year_at(data, kind, len, scan_end, year)) {
            *out_end = scan_end + 4;
            return 1;
        }
    }
    return 0;
}

/* The day-first branch,
   ([0-3]?[0-9])(?:...)?\.?\s+(?:of\s+)?(MONTH)\.?,?\s+(YEAR), at pos, which the
   caller reaches only when CP(pos) is an ASCII digit. The day is greedy (two
   digits before one); the one-digit form always matches, so it needs no guard.
   Returns 1 with day/month/year and the end position. */
static int text_b(const void *data, int kind, Py_ssize_t len, Py_ssize_t pos, int *day, int *month, int *year,
                  Py_ssize_t *out_end) {
    if (pos + 1 < len && CP(pos) <= '3' && is_ascii_digit(CP(pos + 1)) &&
        text_b_tail(data, kind, len, pos + 2, month, year, out_end)) {
        *day = (int)(CP(pos) - '0') * 10 + (int)(CP(pos + 1) - '0');
        return 1;
    }
    if (text_b_tail(data, kind, len, pos + 1, month, year, out_end)) {
        *day = (int)(CP(pos) - '0');
        return 1;
    }
    return 0;
}

/* The first numeric date in the text -- ISO, then the compact stamp, then the
   day-month-year spelling, each tried in turn. ISO and compact fall through to the
   next pattern when their first match is calendar-impossible; day-month-year is the
   last, so its first match (valid or not) settles the answer. Writes (year, month,
   day) and returns 1 on a hit, 0 when the text carries no date. Shared by the
   _date_scan entry point and the <meta> date walk. */
static int scan_first_date(const void *data, int kind, Py_ssize_t len, int current_year, int *year, int *month,
                           int *day) {
    Py_ssize_t end;
    for (Py_ssize_t pos = 0; pos < len; pos++) {
        if (iso_at(data, kind, len, pos, year, month, day, &end)) {
            if (ymd_valid(*year, *month, *day)) {
                return 1;
            }
            break;
        }
    }
    for (Py_ssize_t pos = 0; pos < len; pos++) {
        if ((pos == 0 || !is_ascii_digit(CP(pos - 1))) && compact_at(data, kind, len, pos, year, month, day)) {
            if (ymd_valid(*year, *month, *day)) {
                return 1;
            }
            break;
        }
    }
    for (Py_ssize_t pos = 0; pos < len; pos++) {
        int raw_day, raw_month, raw_year;
        if ((pos == 0 || !is_ascii_digit(CP(pos - 1))) &&
            dmy_at(data, kind, len, pos, &raw_day, &raw_month, &raw_year, &end)) {
            int resolved_year = correct_year(raw_year, current_year);
            if (dmy_resolve(raw_day, raw_month, resolved_year, month, day)) {
                *year = resolved_year;
                return 1;
            }
            break;
        }
    }
    return 0;
}

/* _scan: the first numeric date, or None. */
PyObject *turbohtml_date_scan(PyObject *Py_UNUSED(module), PyObject *args) {
    PyObject *text;
    int current_year;
    if (!PyArg_ParseTuple(args, "Ui:_date_scan", &text, &current_year)) {
        return NULL;
    }
    int year, month, day;
    if (scan_first_date(PyUnicode_DATA(text), PyUnicode_KIND(text), PyUnicode_GET_LENGTH(text), current_year, &year,
                        &month, &day)) {
        return Py_BuildValue("(iii)", year, month, day);
    }
    Py_RETURN_NONE;
}

/* A visitor for scan_every_date: one calendar-valid date, returning -1 to stop the sweep with an error. */
typedef int (*date_visitor)(void *context, int year, int month, int day);

/* Every ISO, day-month-year, and written-out date in the text, each pattern swept independently over the whole
   text and reported in that order. A match whose calendar is impossible is skipped but still advances the scan
   past its span, the way re.finditer does. Returns -1 when the visitor did. */
static int scan_every_date(const void *data, int kind, Py_ssize_t len, int current_year, date_visitor visit,
                           void *context) {
    int year, month, day;
    Py_ssize_t end;
    Py_ssize_t pos = 0;
    while (pos < len) {
        if (iso_at(data, kind, len, pos, &year, &month, &day, &end)) {
            if (ymd_valid(year, month, day) && visit(context, year, month, day) < 0) { /* GCOVR_EXCL_BR_LINE */
                return -1;                                                             /* GCOVR_EXCL_LINE */
            }
            pos = end;
        } else {
            pos++;
        }
    }
    pos = 0;
    while (pos < len) {
        int raw_day, raw_month, raw_year;
        if ((pos == 0 || !is_ascii_digit(CP(pos - 1))) &&
            dmy_at(data, kind, len, pos, &raw_day, &raw_month, &raw_year, &end)) {
            int resolved_year = correct_year(raw_year, current_year);
            if (dmy_resolve(raw_day, raw_month, resolved_year, &month, &day)) {
                if (visit(context, resolved_year, month, day) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                    return -1;                                       /* GCOVR_EXCL_LINE */
                }
            }
            pos = end;
        } else {
            pos++;
        }
    }
    pos = 0;
    while (pos < len) {
        int matched = 0;
        if (CP(pos) >= 'A') { /* a written-out date opens with a month name (letter) */
            matched = text_a(data, kind, len, pos, &month, &day, &year, &end);
        } else if (is_ascii_digit(CP(pos))) { /* or a leading day (digit) */
            matched = text_b(data, kind, len, pos, &day, &month, &year, &end);
        }
        if (!matched) {
            pos++;
            continue;
        }
        if (ymd_valid(year, month, day) && visit(context, year, month, day) < 0) { /* GCOVR_EXCL_BR_LINE */
            return -1;                                                             /* GCOVR_EXCL_LINE */
        }
        pos = end;
    }
    return 0;
}

static int append_ymd(void *context, int year, int month, int day) {
    PyObject *item = Py_BuildValue("(iii)", year, month, day);
    if (item == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int rc = PyList_Append((PyObject *)context, item);
    Py_DECREF(item);
    return rc; /* GCOVR_EXCL_BR_LINE: PyList_Append only fails on allocation failure */
}

/* _scan_all: every date the sweep reports, or an empty list. */
PyObject *turbohtml_date_scan_all(PyObject *Py_UNUSED(module), PyObject *args) {
    PyObject *text;
    int current_year;
    if (!PyArg_ParseTuple(args, "Ui:_date_scan_all", &text, &current_year)) {
        return NULL;
    }
    PyObject *found = PyList_New(0);
    if (found == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;     /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int status = scan_every_date(PyUnicode_DATA(text), PyUnicode_KIND(text), PyUnicode_GET_LENGTH(text), current_year,
                                 append_ymd, found);
    if (status < 0) {     /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_DECREF(found); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;      /* GCOVR_EXCL_LINE */
    }
    return found;
}

/* A URL date, _URL_DATE = (?<!\d)(YEAR)[/_-](MONTH)[/_-](DAY)(?!\d), starting at
   pos (the caller guarantees the (?<!\d) boundary). The trailing (?!\d) forbids a
   digit after the day. */
static int url_at(const void *data, int kind, Py_ssize_t len, Py_ssize_t pos, int *year, int *month, int *day) {
    if (!year_at(data, kind, len, pos, year)) {
        return 0;
    }
    Py_ssize_t cursor = pos + 4;
    if (cursor >= len || !(CP(cursor) == '/' || CP(cursor) == '_' || CP(cursor) == '-')) {
        return 0;
    }
    cursor++;
    if (cursor + 2 < len && month2(CP(cursor), CP(cursor + 1), month) &&
        (CP(cursor + 2) == '/' || CP(cursor + 2) == '_' || CP(cursor + 2) == '-')) {
        cursor += 2;
    } else if (cursor + 1 < len && month1(CP(cursor), month) &&
               (CP(cursor + 1) == '/' || CP(cursor + 1) == '_' || CP(cursor + 1) == '-')) {
        cursor += 1;
    } else {
        return 0;
    }
    cursor++;
    if (cursor + 1 < len && day2(CP(cursor), CP(cursor + 1), day) &&
        (cursor + 2 >= len || !is_ascii_digit(CP(cursor + 2)))) {
        return 1;
    }
    if (cursor < len && day1(CP(cursor), day) && (cursor + 1 >= len || !is_ascii_digit(CP(cursor + 1)))) {
        return 1;
    }
    return 0;
}

/* _url_date: the first URL date pattern, calendar-validated. */
PyObject *turbohtml_date_url(PyObject *Py_UNUSED(module), PyObject *url) {
    if (!PyUnicode_Check(url)) {
        PyErr_SetString(PyExc_TypeError, "_date_url() argument must be str");
        return NULL;
    }
    int kind = PyUnicode_KIND(url);
    const void *data = PyUnicode_DATA(url);
    Py_ssize_t len = PyUnicode_GET_LENGTH(url);
    int year, month, day;
    for (Py_ssize_t pos = 0; pos < len; pos++) {
        if ((pos == 0 || !is_ascii_digit(CP(pos - 1))) && url_at(data, kind, len, pos, &year, &month, &day)) {
            if (ymd_valid(year, month, day)) {
                return Py_BuildValue("(iii)", year, month, day);
            }
            break;
        }
    }
    Py_RETURN_NONE;
}

/* The <meta> date stage of turbohtml.extract.dates.

   Python gathered every meta candidate into a list and then ran the shared _pick
   selection over it; both now run here in one walk. The vocabularies below are
   htmldate's meta key lists (the same tables the Python module held), sorted so a
   lowercased key resolves by binary search. Every entry is lowercase ASCII, so a
   key with a non-ASCII code point -- or one longer than the longest entry -- can
   match neither list and is rejected before the search. */

/* The longest vocabulary entry, "og:article:published_time" and
   "citation_publication_date", both 25 bytes; the fold buffer holds that plus a
   terminator. */
#define MAX_KEY_LEN 25

static const char *const PUBLISHED_KEYS[] = {
    "article.created",
    "article.published",
    "article:published",
    "article:published_time",
    "article_date_original",
    "bt:pubdate",
    "citation_date",
    "citation_publication_date",
    "created",
    "date",
    "date_published",
    "datecreated",
    "dateposted",
    "datepublished",
    "dc.date",
    "dc.date.created",
    "dc.date.issued",
    "dc.date.publication",
    "dcterms.created",
    "dcterms.date",
    "dcterms.issued",
    "og:article:published_time",
    "og:pubdate",
    "og:published_time",
    "parsely-pub-date",
    "pdate",
    "pubdate",
    "publication_date",
    "publish-date",
    "publish_date",
    "published_date",
    "published_time",
    "publisheddate",
    "pubyear",
    "rnews:datepublished",
    "sailthru.date",
    "timestamp",
};

static const char *const MODIFIED_KEYS[] = {
    "article:modified",
    "article:modified_time",
    "article:post_modified",
    "datemodified",
    "dateupdate",
    "dc.modified",
    "dcterms.modified",
    "last-modified",
    "lastdate",
    "lastmod",
    "lastmodified",
    "modificationdate",
    "modified",
    "modified_time",
    "og:article:modified_time",
    "og:modified_time",
    "og:updated_time",
    "revision_date",
    "updated_time",
};

/* The date roles a meta key can carry; 0 is "neither key list matched". */
enum { META_NONE = 0, META_PUBLISHED = 1, META_MODIFIED = 2 };

static int key_set_compare(const void *probe, const void *entry) {
    return strcmp((const char *)probe, *(const char *const *)entry);
}

static int key_in_set(const char *key, const char *const *set, size_t count) {
    return bsearch(key, set, count, sizeof(*set), key_set_compare) != NULL;
}

/* Fold an attribute value to a lowercase-ASCII C string in out, returning 1 on
   success. A code point above U+007F, or a value longer than the longest key,
   cannot equal any (lowercase, ASCII) vocabulary entry, so both short-circuit to 0
   rather than folding. */
static int fold_key(const Py_UCS4 *value, Py_ssize_t len, char *out) {
    if (len > MAX_KEY_LEN) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        Py_UCS4 codepoint = value[index];
        if (codepoint > 0x7F) {
            return 0;
        }
        out[index] = (char)((codepoint >= 'A' && codepoint <= 'Z') ? codepoint + ('a' - 'A') : codepoint);
    }
    out[len] = '\0';
    return 1;
}

/* Which key list, if any, an attribute value falls in. */
static int key_role(const Py_UCS4 *value, Py_ssize_t len) {
    char folded[MAX_KEY_LEN + 1];
    if (value == NULL || !fold_key(value, len, folded)) {
        return META_NONE;
    }
    if (key_in_set(folded, PUBLISHED_KEYS, COUNT_OF(PUBLISHED_KEYS))) {
        return META_PUBLISHED;
    }
    if (key_in_set(folded, MODIFIED_KEYS, COUNT_OF(MODIFIED_KEYS))) {
        return META_MODIFIED;
    }
    return META_NONE;
}

/* The value of a by-name attribute (property, pubdate: neither is an interned
   atom), or NULL when the node has none. */
static const th_node_attr *named_attr(th_tree *tree, th_node *node, const char *name, Py_ssize_t name_len) {
    Py_ssize_t index = th_node_attr_find(tree, node, name, name_len);
    return index < 0 ? NULL : &node->attrs[index];
}

/* The date role of a <meta> element from its name/property/itemprop/http-equiv keys
   and a pubdate="pubdate" flag, publication winning over modification (htmldate's
   _role_of returns the publication role whenever any key carries it). */
static int meta_role(th_tree *tree, th_node *node) {
    const th_node_attr *keys[] = {
        find_node_attr(node, TH_ATTR_NAME),
        named_attr(tree, node, "property", 8),
        find_node_attr(node, TH_ATTR_ITEMPROP),
        find_node_attr(node, TH_ATTR_HTTP_EQUIV),
    };
    int modified = 0;
    for (size_t index = 0; index < COUNT_OF(keys); index++) {
        if (keys[index] == NULL) {
            continue;
        }
        int role = key_role(keys[index]->value, keys[index]->value_len);
        if (role == META_PUBLISHED) {
            return META_PUBLISHED;
        }
        if (role == META_MODIFIED) {
            modified = 1;
        }
    }
    /* pubdate="pubdate" adds the literal key "pubdate", itself a publication key. */
    const th_node_attr *pubdate = named_attr(tree, node, "pubdate", 7);
    char folded[MAX_KEY_LEN + 1];
    if (pubdate != NULL && pubdate->value != NULL && fold_key(pubdate->value, pubdate->value_len, folded) &&
        strcmp(folded, "pubdate") == 0) {
        return META_PUBLISHED;
    }
    return modified ? META_MODIFIED : META_NONE;
}

/* The text a <meta> dates from: its content, or its datetime when content is
   absent or empty (htmldate reads `content or datetime`; an absent or empty
   attribute has a NULL value here). Writes the length and returns the value, or
   NULL when neither is present, which the caller drops. */
static const Py_UCS4 *meta_date_text(th_node *node, Py_ssize_t *out_len) {
    const th_node_attr *content = find_node_attr(node, TH_ATTR_CONTENT);
    if (content != NULL && content->value != NULL) {
        *out_len = content->value_len;
        return content->value;
    }
    const th_node_attr *datetime = find_node_attr(node, TH_ATTR_DATETIME);
    if (datetime != NULL && datetime->value != NULL) {
        *out_len = datetime->value_len;
        return datetime->value;
    }
    *out_len = 0;
    return NULL;
}

/* Whether year-month-day is at least the min bound; the parts compare like the
   ordinals htmldate's window uses, monotonically, so a component compare suffices. */
static int date_at_least(int year, int month, int day, int min_year, int min_month, int min_day) {
    if (year != min_year) {
        return year > min_year;
    }
    if (month != min_month) {
        return month > min_month;
    }
    return day >= min_day;
}

/* A candidate's role. The meta walk's enum names the two signed roles; a date with no publication or modification
   marker is generic and satisfies whichever role the caller wanted. */
enum { DATE_ROLE_GENERIC = 3 };

/* Is (year, month, day) inside the inclusive window? */
static int date_within(int year, int month, int day, const int *low, const int *high) {
    return date_at_least(year, month, day, low[0], low[1], low[2]) &&
           date_at_least(high[0], high[1], high[2], year, month, day);
}

/* One stage's answer: the wanted-role date if the stage found one, else the first off-role date it saw. htmldate
   returns the first wanted-role hit and falls back to whatever else the stage offered. */
typedef struct {
    int wanted;
    int reserve;
    int chosen[3];
    int spare[3];
} date_pick;

/* Offer a candidate to the pick. Returns 1 once the wanted role is settled and the stage can stop. */
static int pick_offer(date_pick *pick, int role, int year, int month, int day, int want) {
    if (role == want || role == DATE_ROLE_GENERIC) {
        pick->wanted = 1;
        pick->chosen[0] = year;
        pick->chosen[1] = month;
        pick->chosen[2] = day;
        return 1;
    }
    if (!pick->reserve) {
        pick->reserve = 1;
        pick->spare[0] = year;
        pick->spare[1] = month;
        pick->spare[2] = day;
    }
    return 0;
}

/* The date the pick settled on, or NULL when the stage found nothing in the window. */
static const int *pick_result(const date_pick *pick) {
    if (pick->wanted) {
        return pick->chosen;
    }
    return pick->reserve ? pick->spare : NULL;
}

/* The value of the named attribute as code points, or NULL. A tokenized attribute (class) is joined on the fly by
   the caller, so this hands back the raw storage. */
static const Py_UCS4 *attr_text(th_tree *tree, th_node *node, const char *name, Py_ssize_t *out_len) {
    const th_node_attr *found = named_attr(tree, node, name, (Py_ssize_t)strlen(name));
    if (found == NULL) {
        return NULL;
    }
    *out_len = found->value_len;
    return found->value;
}

/* Does the lowercased attribute text hold `needle`? Used for the class/id/itemprop marker vocabulary, which
   htmldate matches as a case-insensitive substring rather than a whole token. */
static int text_holds(const Py_UCS4 *text, Py_ssize_t len, const char *needle) {
    Py_ssize_t width = (Py_ssize_t)strlen(needle);
    for (Py_ssize_t start = 0; start + width <= len; start++) {
        Py_ssize_t offset = 0;
        while (offset < width && lower_ascii(text[start + offset]) == (Py_UCS4)(unsigned char)needle[offset]) {
            offset++;
        }
        if (offset == width) {
            return 1;
        }
    }
    return 0;
}

/* Does any of `words` appear in the attribute text? */
static int text_holds_any(const Py_UCS4 *text, Py_ssize_t len, const char *const *words, size_t count) {
    for (size_t index = 0; index < count; index++) {
        if (text_holds(text, len, words[index])) {
            return 1;
        }
    }
    return 0;
}

/* The <meta> stage: a candidate of the wanted role ends the walk, the first in-window off-role date is the reserve. */
static void dates_meta_stage(th_tree *tree, th_node *root, const int *low, const int *high, int current_year, int want,
                             date_pick *pick) {
    for (th_node *node = root->first_child; node != NULL; node = preorder_next(node, root)) {
        if (node->type != TH_NODE_ELEMENT || node->atom != TH_TAG_META) {
            continue;
        }
        int role = meta_role(tree, node);
        if (role == META_NONE) {
            continue;
        }
        Py_ssize_t len;
        const Py_UCS4 *text = meta_date_text(node, &len);
        if (text == NULL) {
            continue;
        }
        int year, month, day;
        if (!scan_first_date(text, PyUnicode_4BYTE_KIND, len, current_year, &year, &month, &day) ||
            !date_within(year, month, day, low, high)) {
            continue;
        }
        if (pick_offer(pick, role, year, month, day, want)) {
            return;
        }
    }
}

/* The URL stage: the /YYYY/MM/DD/ a canonical link or og:url carries, the fastest and most trustworthy signal. */
static void dates_url_stage(th_tree *tree, th_node *root, const int *low, const int *high, int want, date_pick *pick) {
    for (th_node *node = root->first_child; node != NULL; node = preorder_next(node, root)) {
        if (node->type != TH_NODE_ELEMENT) {
            continue;
        }
        const Py_UCS4 *url = NULL;
        Py_ssize_t len = 0;
        if (node->atom == TH_TAG_LINK) {
            Py_ssize_t rel_len = 0;
            const Py_UCS4 *rel = attr_text(tree, node, "rel", &rel_len);
            if (rel != NULL && text_holds(rel, rel_len, "canonical")) {
                url = attr_text(tree, node, "href", &len);
            }
        } else if (node->atom == TH_TAG_META) {
            Py_ssize_t key_len = 0;
            const Py_UCS4 *key = attr_text(tree, node, "property", &key_len);
            if (key == NULL) {
                key = attr_text(tree, node, "name", &key_len);
            }
            if (key != NULL && key_len == 6 && text_holds(key, key_len, "og:url")) {
                url = attr_text(tree, node, "content", &len);
            }
        }
        if (url == NULL) {
            continue;
        }
        for (Py_ssize_t pos = 0; pos < len; pos++) {
            int year, month, day;
            if (!url_at(url, PyUnicode_4BYTE_KIND, len, pos, &year, &month, &day)) {
                continue;
            }
            if (date_within(year, month, day, low, high)) {
                pick_offer(pick, DATE_ROLE_GENERIC, year, month, day, want);
                return;
            }
            break;
        }
    }
}

/* The scan_every_date visitor that keeps the first report: the first date of any spelling in a block of element
   text, written-out months included. */
typedef struct {
    int found;
    int year;
    int month;
    int day;
} first_date_hit;

static int first_date(void *context, int year, int month, int day) {
    first_date_hit *hit = context;
    if (!hit->found) {
        hit->found = 1;
        hit->year = year;
        hit->month = month;
        hit->day = day;
    }
    return 0;
}

/* The class/id/itemprop vocabulary of the temporal-markup stage, drawn from htmldate's but kept to the
   high-precision markers (skipping its broad info/author/footer catch-alls, which pull in unrelated dates). Each
   is matched as a case-insensitive substring of the attribute, the way a [class*=word i] selector reads it. */
static const char *const CLASS_MARKERS[] = {"date",   "datum",    "publish",    "posted",  "pubdate",  "timestamp",
                                            "byline", "dateline", "entry-date", "updated", "modified", "created"};
static const char *const ID_MARKERS[] = {"date", "publish", "posted", "timestamp"};
static const char *const PUBLISHED_MARKERS[] = {"publish",  "posted",  "pubdate", "entry-date",
                                                "dateline", "created", "byline"};
static const char *const MODIFIED_MARKERS[] = {"updated", "modified", "lastmod", "revised"};

typedef struct {
    const Py_UCS4 *classes;
    Py_ssize_t class_len;
    const Py_UCS4 *identity;
    Py_ssize_t id_len;
    const Py_UCS4 *prop;
    Py_ssize_t prop_len;
} date_markers;

static int markers_temporal(th_node *node, const date_markers *markers) {
    if (node->atom == TH_TAG_TIME) {
        return 1;
    }
    if (markers->classes != NULL &&
        text_holds_any(markers->classes, markers->class_len, CLASS_MARKERS, COUNT_OF(CLASS_MARKERS))) {
        return 1;
    }
    if (markers->identity != NULL &&
        text_holds_any(markers->identity, markers->id_len, ID_MARKERS, COUNT_OF(ID_MARKERS))) {
        return 1;
    }
    return markers->prop != NULL && text_holds(markers->prop, markers->prop_len, "date");
}

static int markers_hold_any(const date_markers *markers, const char *const *words, size_t count) {
    if (markers->classes != NULL && text_holds_any(markers->classes, markers->class_len, words, count)) {
        return 1;
    }
    return markers->identity != NULL && text_holds_any(markers->identity, markers->id_len, words, count);
}

static int markers_role(th_tree *tree, th_node *node, const date_markers *markers) {
    if (named_attr(tree, node, "pubdate", 7) != NULL) {
        return META_PUBLISHED;
    }
    if (markers_hold_any(markers, PUBLISHED_MARKERS, COUNT_OF(PUBLISHED_MARKERS))) {
        return META_PUBLISHED;
    }
    if (markers_hold_any(markers, MODIFIED_MARKERS, COUNT_OF(MODIFIED_MARKERS))) {
        return META_MODIFIED;
    }
    return DATE_ROLE_GENERIC;
}

/* The temporal-markup stage: <time> elements, and elements whose class/id/itemprop marks them as a date. A
   <time datetime> is the canonical spelling, but many pages date an article in a <span class="published"> or
   <p class="entry-date"> instead. Each element contributes the date in its datetime/title attribute or its first
   text date. Returns -1 on allocation failure. */
static int dates_time_stage(th_tree *tree, th_node *root, const int *low, const int *high, int current_year, int want,
                            date_pick *pick) {
    for (th_node *node = root->first_child; node != NULL; node = preorder_next(node, root)) {
        if (node->type != TH_NODE_ELEMENT) {
            continue;
        }
        date_markers markers = {0};
        markers.classes = attr_text(tree, node, "class", &markers.class_len);
        markers.identity = attr_text(tree, node, "id", &markers.id_len);
        markers.prop = attr_text(tree, node, "itemprop", &markers.prop_len);
        if (!markers_temporal(node, &markers)) {
            continue;
        }
        Py_ssize_t raw_len = 0;
        const Py_UCS4 *raw = attr_text(tree, node, "datetime", &raw_len);
        if (raw == NULL) {
            raw = attr_text(tree, node, "title", &raw_len);
        }
        first_date_hit hit = {0};
        if (raw != NULL) {
            hit.found =
                scan_first_date(raw, PyUnicode_4BYTE_KIND, raw_len, current_year, &hit.year, &hit.month, &hit.day);
        } else {
            Py_ssize_t text_len = 0;
            Py_UCS4 *text = th_node_text(tree, node, &text_len);
            if (text == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            (void)scan_every_date(text, PyUnicode_4BYTE_KIND, text_len, current_year, first_date, &hit);
            PyMem_Free(text);
        }
        if (!hit.found || !date_within(hit.year, hit.month, hit.day, low, high)) {
            continue;
        }
        if (pick_offer(pick, markers_role(tree, node, &markers), hit.year, hit.month, hit.day, want)) {
            return 0;
        }
    }
    return 0;
}

/* Offer the datePublished/dateModified strings of one JSON-LD node to the pick, recursing through @graph, nested
   objects and lists in the order json.loads produced them. Returns 1 once the wanted role is settled. */
static int dates_json_node(PyObject *node, const int *low, const int *high, int current_year, int want,
                           date_pick *pick) {
    if (PyList_Check(node)) {
        for (Py_ssize_t index = 0; index < PyList_GET_SIZE(node); index++) {
            if (dates_json_node(PyList_GET_ITEM(node, index), low, high, current_year, want, pick)) {
                return 1;
            }
        }
        return 0;
    }
    if (!PyDict_Check(node)) {
        return 0;
    }
    static const char *const keys[] = {"datePublished", "dateModified"};
    static const int roles[] = {META_PUBLISHED, META_MODIFIED};
    for (size_t index = 0; index < COUNT_OF(keys); index++) {
        PyObject *value = PyDict_GetItemString(node, keys[index]);
        if (value == NULL || !PyUnicode_Check(value)) {
            continue;
        }
        int year, month, day;
        if (scan_first_date(PyUnicode_DATA(value), PyUnicode_KIND(value), PyUnicode_GET_LENGTH(value), current_year,
                            &year, &month, &day) &&
            date_within(year, month, day, low, high) && pick_offer(pick, roles[index], year, month, day, want)) {
            return 1;
        }
    }
    Py_ssize_t position = 0;
    PyObject *key, *value;
    while (PyDict_Next(node, &position, &key, &value)) {
        if ((PyList_Check(value) || PyDict_Check(value)) &&
            dates_json_node(value, low, high, current_year, want, pick)) {
            return 1;
        }
    }
    return 0;
}

/* The text stage's tally: how often each in-window date recurs across the visible text, keyed by the ordinal
   year * 10000 + month * 100 + day so a tie breaks by comparing keys. */
typedef struct {
    const int *low;
    const int *high;
    int *keys;
    int *counts;
    Py_ssize_t size;
    Py_ssize_t capacity;
} date_tally;

static int tally_date(void *context, int year, int month, int day) {
    date_tally *tally = context;
    if (!date_within(year, month, day, tally->low, tally->high)) {
        return 0;
    }
    int key = year * 10000 + month * 100 + day;
    for (Py_ssize_t index = 0; index < tally->size; index++) {
        if (tally->keys[index] == key) {
            tally->counts[index]++;
            return 0;
        }
    }
    if (tally->size == tally->capacity) {
        Py_ssize_t grown = tally->capacity == 0 ? 16 : tally->capacity * 2;
        int *keys = PyMem_Realloc(tally->keys, (size_t)grown * sizeof(*keys));
        if (keys == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        tally->keys = keys;
        int *counts = PyMem_Realloc(tally->counts, (size_t)grown * sizeof(*counts));
        if (counts == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;        /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        tally->counts = counts;
        tally->capacity = grown;
    }
    tally->keys[tally->size] = key;
    tally->counts[tally->size] = 1;
    tally->size++;
    return 0;
}

/* Does the tallied date at `index` beat the one at `best`: more occurrences, or as many and earlier when a
   publication date is wanted, later otherwise? */
static int tally_prefers(const date_tally *tally, Py_ssize_t index, Py_ssize_t best, int want) {
    if (tally->counts[index] != tally->counts[best]) {
        return tally->counts[index] > tally->counts[best];
    }
    if (want == META_PUBLISHED) {
        return tally->keys[index] < tally->keys[best];
    }
    return tally->keys[index] > tally->keys[best];
}

/* The extensive last resort: the date that recurs most across the body's visible text. Boilerplate pages carry no
   date metadata, so the publication date is only in the prose -- but so is every comment and archive link; the
   modal date (the byline, a permalink, a caption, the dateline) is the one htmldate's reference scoring settles
   on. A tie breaks toward the earliest date when a publication date is wanted and the latest otherwise. Returns -1
   on allocation failure. */
static int dates_text_stage(th_tree *tree, th_node *root, const int *low, const int *high, int current_year, int want,
                            date_pick *pick) {
    date_tally tally = {low, high, NULL, NULL, 0, 0};
    int status = 0;
    for (th_node *node = root->first_child; node != NULL; node = preorder_next(node, root)) {
        if (node->type != TH_NODE_ELEMENT || node->atom != TH_TAG_BODY) {
            continue;
        }
        Py_ssize_t len = 0;
        Py_UCS4 *text = th_node_text(tree, node, &len);
        if (text == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            status = -1;    /* GCOVR_EXCL_LINE: allocation-failure path */
            break;          /* GCOVR_EXCL_LINE */
        }
        status = scan_every_date(text, PyUnicode_4BYTE_KIND, len, current_year, tally_date, &tally);
        PyMem_Free(text);
        if (status < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            break;        /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    if (status < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        goto done;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (tally.size > 0) {
        Py_ssize_t best = 0;
        for (Py_ssize_t index = 1; index < tally.size; index++) {
            if (tally_prefers(&tally, index, best, want)) {
                best = index;
            }
        }
        int key = tally.keys[best];
        pick_offer(pick, DATE_ROLE_GENERIC, key / 10000, key / 100 % 100, key % 100, want);
    }
done:
    PyMem_Free(tally.keys);
    PyMem_Free(tally.counts);
    return status;
}

/* The markup stages that need the tree: <time> elements, then (with extensive) the visible text. Returns -1 on
   allocation failure. */
static int dates_markup_stages(th_tree *tree, th_node *root, const int *low, const int *high, int current_year,
                               int want, int extensive, date_pick *pick, const char **signal) {
    *signal = "time";
    if (dates_time_stage(tree, root, low, high, current_year, want, pick) < 0) { /* GCOVR_EXCL_BR_LINE: alloc */
        return -1;                                                               /* GCOVR_EXCL_LINE */
    }
    if (pick_result(pick) != NULL || !extensive) {
        return 0;
    }
    *signal = "text";
    return dates_text_stage(tree, root, low, high, current_year, want, pick);
}

/* Document._dates(want, current_year, min_y, min_m, min_d, max_y, max_m, max_d, extensive)
   -> (year, month, day, signal) or None.

   The whole of turbohtml.extract.dates after parsing: the signals tried in htmldate's order -- a date in the
   canonical URL, then publication/modification <meta> tags, then JSON-LD, then <time> elements, then (with
   extensive) visible text -- and the first stage that yields a date inside [min, max] wins. want is META_PUBLISHED
   (original=True) or META_MODIFIED; within a stage a candidate of the wanted or generic role wins on sight and the
   first off-role one is the reserve. The tree-walking stages run under the document's critical section. */
PyObject *turbohtml_document_dates(PyObject *self, PyObject *args) {
    int want, current_year, extensive;
    int low[3], high[3];
    if (!PyArg_ParseTuple(args, "iiiiiiiip:_dates", &want, &current_year, &low[0], &low[1], &low[2], &high[0], &high[1],
                          &high[2], &extensive)) {
        return NULL;
    }
    th_tree *tree = tree_of(self);
    th_node *root = ((NodeObject *)self)->node;
    date_pick pick = {0};
    const char *signal = "url";
    const int *found;
    Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
    dates_url_stage(tree, root, low, high, want, &pick);
    if ((found = pick_result(&pick)) == NULL) {
        signal = "meta";
        dates_meta_stage(tree, root, low, high, current_year, want, &pick);
        found = pick_result(&pick);
    }
    Py_END_CRITICAL_SECTION();
    if (found == NULL) {
        signal = "json-ld";
        PyObject *blocks = turbohtml_document_json_ld(self, NULL); /* runs Python code, so outside the section */
        if (blocks == NULL) {
            return NULL;
        }
        dates_json_node(blocks, low, high, current_year, want, &pick);
        Py_DECREF(blocks);
        found = pick_result(&pick);
    }
    if (found == NULL) {
        int status;
        Py_BEGIN_CRITICAL_SECTION(((NodeObject *)self)->handle);
        status = dates_markup_stages(tree, root, low, high, current_year, want, extensive, &pick, &signal);
        Py_END_CRITICAL_SECTION();
        if (status < 0) {            /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        found = pick_result(&pick);
    }
    if (found == NULL) {
        Py_RETURN_NONE;
    }
    return Py_BuildValue("(iiis)", found[0], found[1], found[2], signal);
}
