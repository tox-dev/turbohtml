/* The value a form control submits, per WHATWG "constructing the entry list"
   (https://html.spec.whatwg.org/multipage/form-control-infrastructure.html#constructing-the-form-data-set).

   An input submits its value, which is the value attribute after the value sanitization algorithm of the input's type
   state (https://html.spec.whatwg.org/multipage/input.html): text-like states strip newlines, url and email also trim
   ASCII whitespace, a multiple email trims each comma-separated address, number and the date/time states empty a value
   that is not a valid string of their microsyntax, datetime-local rewrites a valid value to its normalized form, and
   range falls back to its default value and then clamps to min, max, and step. Hidden keeps the attribute verbatim, and
   so does color: its algorithm parses the value as a CSS color and serializes the result, which needs a full CSS color
   parser this module does not carry. A textarea submits its API value, the raw text with newlines normalized to LF. */

#include "dom/form_value.h"
#include "core/ascii.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifndef TH_NOINLINE
#if defined(_MSC_VER)
#define TH_NOINLINE __declspec(noinline)
#elif defined(__GNUC__) || defined(__clang__)
#define TH_NOINLINE __attribute__((noinline))
#else
#define TH_NOINLINE
#endif
#endif

enum input_state {
    STATE_TEXT, /* text, search, tel, password, and every unrecognized type */
    STATE_VERBATIM,
    STATE_URL,
    STATE_EMAIL,
    STATE_NUMBER,
    STATE_RANGE,
    STATE_DATE,
    STATE_MONTH,
    STATE_WEEK,
    STATE_TIME,
    STATE_DATETIME_LOCAL,
};

/* Whether the code-point run equals the lowercase ASCII literal, ignoring ASCII case. */
static int ascii_ieq(const Py_UCS4 *value, Py_ssize_t len, const char *literal) {
    if (len != (Py_ssize_t)strlen(literal)) {
        return 0;
    }
    for (Py_ssize_t index = 0; index < len; index++) {
        if (lower_ascii(value[index]) != (Py_UCS4)(unsigned char)literal[index]) {
            return 0;
        }
    }
    return 1;
}

static enum input_state input_state_of(const th_node_attr *type) {
    static const struct {
        const char *name;
        Py_ssize_t len;
        enum input_state state;
    } STATES[] = {
        {"hidden", 6, STATE_VERBATIM},
        {"color", 5, STATE_VERBATIM},
        {"url", 3, STATE_URL},
        {"email", 5, STATE_EMAIL},
        {"number", 6, STATE_NUMBER},
        {"range", 5, STATE_RANGE},
        {"date", 4, STATE_DATE},
        {"month", 5, STATE_MONTH},
        {"week", 4, STATE_WEEK},
        {"time", 4, STATE_TIME},
        {"datetime-local", 14, STATE_DATETIME_LOCAL},
    };
    for (size_t index = 0; index < sizeof(STATES) / sizeof(STATES[0]); index++) {
        if (type->value_len == STATES[index].len && ascii_ieq(type->value, type->value_len, STATES[index].name)) {
            return STATES[index].state;
        }
    }
    return STATE_TEXT;
}

static int char_at(const Py_UCS4 *text, Py_ssize_t len, Py_ssize_t pos, Py_UCS4 expected) {
    return pos < len && text[pos] == expected;
}

static Py_ssize_t digit_run(const Py_UCS4 *text, Py_ssize_t len, Py_ssize_t pos) {
    Py_ssize_t end = pos;
    while (end < len && is_ascii_digit(text[end])) {
        end++;
    }
    return end - pos;
}

/* The value of exactly two ASCII digits at pos when it is at most max, else -1. */
static int two_digits(const Py_UCS4 *text, Py_ssize_t len, Py_ssize_t pos, int max) {
    if (pos + 2 > len || !is_ascii_digit(text[pos]) || !is_ascii_digit(text[pos + 1])) {
        return -1;
    }
    int value = (int)(text[pos] - '0') * 10 + (int)(text[pos + 1] - '0');
    return value > max ? -1 : value;
}

/* The end of a year (four or more ASCII digits, greater than zero) at pos, or -1. *cycle receives the year modulo 400:
   the Gregorian calendar repeats every 400 years, so that alone fixes the leap year and the weekday of 1 January for a
   year of any length. */
static Py_ssize_t parse_year(const Py_UCS4 *text, Py_ssize_t len, Py_ssize_t pos, int *cycle) {
    Py_ssize_t count = digit_run(text, len, pos);
    if (count < 4) {
        return -1;
    }
    int remainder = 0;
    int nonzero = 0;
    for (Py_ssize_t index = pos; index < pos + count; index++) {
        int digit = (int)(text[index] - '0');
        remainder = (remainder * 10 + digit) % 400;
        nonzero |= digit;
    }
    if (!nonzero) {
        return -1;
    }
    *cycle = remainder;
    return pos + count;
}

static int is_leap(int cycle) {
    return cycle % 4 == 0 && (cycle % 100 != 0 || cycle == 0);
}

/* The end of a month component ("YYYY-MM") at pos, or -1. */
static Py_ssize_t parse_month_at(const Py_UCS4 *text, Py_ssize_t len, Py_ssize_t pos, int *cycle, int *month) {
    Py_ssize_t end = parse_year(text, len, pos, cycle);
    if (end < 0 || !char_at(text, len, end, '-')) {
        return -1;
    }
    *month = two_digits(text, len, end + 1, 12);
    return *month < 1 ? -1 : end + 3;
}

/* The end of a date component ("YYYY-MM-DD", the day within its month) at pos, or -1. */
static Py_ssize_t parse_date_at(const Py_UCS4 *text, Py_ssize_t len, Py_ssize_t pos) {
    static const int DAYS[] = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
    int cycle = 0;
    int month = 0;
    Py_ssize_t end = parse_month_at(text, len, pos, &cycle, &month);
    if (end < 0 || !char_at(text, len, end, '-')) {
        return -1;
    }
    int days = DAYS[month - 1] + (month == 2 && is_leap(cycle));
    return two_digits(text, len, end + 1, days) < 1 ? -1 : end + 3;
}

static int valid_week(const Py_UCS4 *text, Py_ssize_t len) {
    int cycle = 0;
    Py_ssize_t end = parse_year(text, len, 0, &cycle);
    if (end < 0 || !char_at(text, len, end, '-') || !char_at(text, len, end + 1, 'W')) {
        return 0;
    }
    /* Gauss's weekday of 1 January (0 is Sunday); a year has 53 ISO weeks when it starts on a Thursday, or on a
       Wednesday in a leap year */
    int prior = (cycle == 0 ? 400 : cycle) - 1;
    int weekday = (1 + 5 * (prior % 4) + 4 * (prior % 100) + 6 * prior) % 7;
    int weeks = weekday == 4 || (weekday == 3 && is_leap(cycle)) ? 53 : 52;
    return two_digits(text, len, end + 2, weeks) >= 1 && end + 4 == len;
}

typedef struct {
    int second;
    Py_ssize_t fraction; /* start of the fractional-second digits */
    Py_ssize_t fraction_len;
} time_shape;

/* The end of a time component ("HH:MM", optionally ":SS" and a one-to-three-digit fraction) at pos, or -1. */
static Py_ssize_t parse_time_at(const Py_UCS4 *text, Py_ssize_t len, Py_ssize_t pos, time_shape *shape) {
    if (two_digits(text, len, pos, 23) < 0 || !char_at(text, len, pos + 2, ':') ||
        two_digits(text, len, pos + 3, 59) < 0) {
        return -1;
    }
    Py_ssize_t end = pos + 5;
    shape->second = 0;
    shape->fraction = end;
    shape->fraction_len = 0;
    if (!char_at(text, len, end, ':')) {
        return end;
    }
    shape->second = two_digits(text, len, end + 1, 59);
    if (shape->second < 0) {
        return -1;
    }
    end += 3;
    if (!char_at(text, len, end, '.')) {
        return end;
    }
    Py_ssize_t count = digit_run(text, len, end + 1);
    if (count < 1 || count > 3) {
        return -1;
    }
    shape->fraction = end + 1;
    shape->fraction_len = count;
    return end + 1 + count;
}

/* A valid local date and time string rewritten to its normalized form ("T" separator, the shortest time that keeps
   the seconds and fraction), or the empty string when the value is not one. */
static PyObject *datetime_local_value(const Py_UCS4 *text, Py_ssize_t len) {
    time_shape shape;
    Py_ssize_t date_end = parse_date_at(text, len, 0);
    if (date_end < 0 || !(char_at(text, len, date_end, 'T') || char_at(text, len, date_end, ' ')) ||
        parse_time_at(text, len, date_end + 1, &shape) != len) {
        return PyUnicode_New(0, 0);
    }
    while (shape.fraction_len > 0 && text[shape.fraction + shape.fraction_len - 1] == '0') {
        shape.fraction_len--;
    }
    Py_UCS4 *buffer = PyMem_Malloc((size_t)len * sizeof(Py_UCS4));
    if (buffer == NULL) {        /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    memcpy(buffer, text, (size_t)(date_end + 6) * sizeof(Py_UCS4));
    buffer[date_end] = 'T';
    Py_ssize_t out = date_end + 6;
    if (shape.second != 0 || shape.fraction_len > 0) {
        memcpy(buffer + out, text + out, 3 * sizeof(Py_UCS4));
        out += 3;
    }
    if (shape.fraction_len > 0) {
        buffer[out++] = '.';
        memcpy(buffer + out, text + shape.fraction, (size_t)shape.fraction_len * sizeof(Py_UCS4));
        out += shape.fraction_len;
    }
    PyObject *result = ucs4_to_str(buffer, out);
    PyMem_Free(buffer);
    return result;
}

/* Whether the value is a valid floating-point number: an optional "-", digits with an optional "." and fraction
   digits (or "." and fraction digits alone), and an optional exponent. */
static int valid_float(const Py_UCS4 *text, Py_ssize_t len) {
    Py_ssize_t pos = char_at(text, len, 0, '-');
    Py_ssize_t whole = digit_run(text, len, pos);
    pos += whole;
    if (char_at(text, len, pos, '.')) {
        Py_ssize_t fraction = digit_run(text, len, pos + 1);
        if (fraction == 0) {
            return 0;
        }
        pos += 1 + fraction;
    } else if (whole == 0) {
        return 0;
    }
    if (char_at(text, len, pos, 'e') || char_at(text, len, pos, 'E')) {
        pos++;
        pos += char_at(text, len, pos, '-') || char_at(text, len, pos, '+');
        Py_ssize_t exponent = digit_run(text, len, pos);
        if (exponent == 0) {
            return 0;
        }
        pos += exponent;
    }
    return pos == len;
}

/* Append the ASCII digit run at *pos to out, advancing both. */
static void copy_digits(const Py_UCS4 *text, Py_ssize_t len, Py_ssize_t *pos, char *out, size_t *out_len) {
    while (*pos < len && is_ascii_digit(text[*pos])) {
        out[(*out_len)++] = (char)text[(*pos)++];
    }
}

/* The WHATWG rules for parsing floating-point number values: leading ASCII whitespace, an optional sign, digits and
   an optional fraction and exponent, trailing text ignored. The matched number is rewritten as a plain ASCII literal
   and converted with correct rounding, which is the spec's "closest double" step. Returns 1 with *out set, 0 when the
   value is not a number or overflows the double range, -1 with an exception on allocation failure. */
static int parse_float_rules(const Py_UCS4 *text, Py_ssize_t len, double *out) {
    Py_ssize_t pos = 0;
    while (pos < len && is_space(text[pos])) {
        pos++;
    }
    int negative = char_at(text, len, pos, '-');
    pos += negative || char_at(text, len, pos, '+');
    int leading_point = char_at(text, len, pos, '.') && pos + 1 < len && is_ascii_digit(text[pos + 1]);
    if (!leading_point && !(pos < len && is_ascii_digit(text[pos]))) {
        return 0;
    }
    /* the literal is at most the input plus a sign, a leading zero, and the terminator */
    char *literal = PyMem_Malloc((size_t)len + 3);
    if (literal == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyErr_NoMemory();  /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;         /* GCOVR_EXCL_LINE */
    }
    size_t used = 0;
    if (negative) {
        literal[used++] = '-';
    }
    literal[used++] = '0';
    copy_digits(text, len, &pos, literal, &used);
    if (char_at(text, len, pos, '.')) {
        pos++;
        if (pos < len && is_ascii_digit(text[pos])) {
            literal[used++] = '.';
            copy_digits(text, len, &pos, literal, &used);
        }
    }
    if (char_at(text, len, pos, 'e') || char_at(text, len, pos, 'E')) {
        Py_ssize_t mark = pos + 1;
        int exponent_negative = char_at(text, len, mark, '-');
        mark += exponent_negative || char_at(text, len, mark, '+');
        if (mark < len && is_ascii_digit(text[mark])) {
            literal[used++] = 'e';
            if (exponent_negative) {
                literal[used++] = '-';
            }
            copy_digits(text, len, &mark, literal, &used);
        }
    }
    literal[used] = '\0';
    /* an explicit OverflowError keeps CPython and PyPy alike: PyPy raises one even when asked not to */
    double value = PyOS_string_to_double(literal, NULL, PyExc_OverflowError);
    PyMem_Free(literal);
    if (PyErr_Occurred()) {
        if (!PyErr_ExceptionMatches(PyExc_OverflowError)) { /* GCOVR_EXCL_BR_LINE: anything else is an OOM */
            return -1;                                      /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        PyErr_Clear(); /* past the double range, which the spec's rounding step reports as an error */
        return 0;
    }
    *out = value == 0.0 ? 0.0 : value; /* the spec's number set has no negative zero */
    return 1;
}

/* A number attribute parsed by the floating-point rules: 1 with *out set, 0 when absent or not a number, -1 with an
   exception on allocation failure. */
static int attr_number(th_node *input, uint32_t atom, double *out) {
    const th_node_attr *attr = find_node_attr(input, atom);
    if (attr == NULL || attr->value == NULL) {
        return 0;
    }
    return parse_float_rules(attr->value, attr->value_len, out);
}

/* A finite nonzero double's shortest round-trip decimal digits (no leading or trailing zeros) and the position of the
   decimal point relative to them: the value is 0.DIGITS times ten to the point. */
typedef struct {
    char digits[32];
    int count;
    int point;
} decimal;

static int decompose(double value, decimal *out) {
    char *repr = PyOS_double_to_string(fabs(value), 'r', 0, 0, NULL);
    if (repr == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    out->count = 0;
    out->point = 0;
    int after_point = 0;
    const char *cursor = repr;
    for (; *cursor != '\0' && *cursor != 'e'; cursor++) {
        if (*cursor == '.') {
            after_point = 1;
        } else if (out->count == 0 && *cursor == '0') {
            out->point -= after_point;
        } else {
            out->digits[out->count++] = *cursor;
            out->point += !after_point;
        }
    }
    if (*cursor == 'e') {
        out->point += (int)strtol(cursor + 1, NULL, 10);
    }
    PyMem_Free(repr);
    while (out->digits[out->count - 1] == '0') {
        out->count--;
    }
    return 0;
}

/* The best representation of a number as a floating-point number, which is ECMAScript's Number::toString. */
static PyObject *number_to_str(double value) {
    if (value == 0.0) {
        return PyUnicode_FromString("0");
    }
    decimal parts;
    if (decompose(value, &parts) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;                    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    char text[64];
    int used = 0;
    if (value < 0) {
        text[used++] = '-';
    }
    int count = parts.count;
    int point = parts.point;
    if (count <= point && point <= 21) {
        memcpy(text + used, parts.digits, (size_t)count);
        memset(text + used + count, '0', (size_t)(point - count));
        used += point;
    } else if (0 < point && point <= 21) {
        memcpy(text + used, parts.digits, (size_t)point);
        text[used + point] = '.';
        memcpy(text + used + point + 1, parts.digits + point, (size_t)(count - point));
        used += count + 1;
    } else if (-6 < point && point <= 0) {
        text[used++] = '0';
        text[used++] = '.';
        memset(text + used, '0', (size_t)-point);
        used -= point;
        memcpy(text + used, parts.digits, (size_t)count);
        used += count;
    } else {
        text[used++] = parts.digits[0];
        if (count > 1) {
            text[used++] = '.';
            memcpy(text + used, parts.digits + 1, (size_t)(count - 1));
            used += count - 1;
        }
        used += snprintf(text + used, sizeof(text) - (size_t)used, "e%+d", point - 1);
    }
    return PyUnicode_FromStringAndSize(text, used);
}

/* The digits after the decimal point in a number's shortest decimal form. */
static int fraction_digits(double value) {
    decimal parts;
    if (value == 0.0 || decompose(value, &parts) < 0) { /* GCOVR_EXCL_BR_LINE: decompose fails only on OOM */
        return 0;
    }
    return parts.count > parts.point ? parts.count - parts.point : 0;
}

/* value rounded to the given count of decimals, which snaps binary noise (0 + 3 * 0.1) onto the decimal a browser's
   decimal arithmetic produces (0.3). Past 15 decimals a double has no spare precision to round away. */
static double round_decimals(double value, int decimals) {
    if (decimals > 15) {
        return value;
    }
    double scale = pow(10.0, decimals);
    return round(value * scale) / scale;
}

static int larger(int left, int right) {
    return left > right ? left : right;
}

/* A number with `scale` decimals as an integer count of its last decimal place; a scale of 1 leaves it as is. */
static double scaled(double value, double scale) {
    return scale > 1.0 ? round(value * scale) : value;
}

static int in_range(double value, double minimum, double maximum) {
    return value >= minimum && (maximum < minimum || value <= maximum);
}

/* The number nearest to value that sits a whole number of steps from base and inside [minimum, maximum] (the upper
   bound only when maximum is not below minimum), the nearer-to-positive-infinity one on a tie, or value unchanged when
   it is already aligned or no aligned number fits. */
static double step_align(double value, double base, double step, double minimum, double maximum) {
    /* Scaling by the decimals the three numbers carry turns them into integers, so 0.35 with step 0.1 is exactly 3.5
       steps (a tie that rounds up) rather than binary 3.4999...; past 15 decimals the numbers stay as they are. */
    int decimals = larger(fraction_digits(value), larger(fraction_digits(base), fraction_digits(step)));
    double scale = decimals <= 15 ? pow(10.0, decimals) : 1.0;
    double scaled_value = scaled(value, scale);
    double scaled_base = scaled(base, scale);
    double scaled_step = scaled(step, scale);
    double steps = (scaled_value - scaled_base) / scaled_step;
    double nearest = floor(steps + 0.5);
    /* a remainder under 2^-46 of a step is binary rounding noise, not a mismatch */
    if (fabs(steps - nearest) <= ldexp(1.0, -46)) {
        return value;
    }
    double candidates[2] = {nearest, nearest > steps ? nearest - 1 : nearest + 1};
    for (size_t index = 0; index < 2; index++) {
        double candidate = (scaled_base + candidates[index] * scaled_step) / scale;
        if (in_range(candidate, minimum, maximum)) {
            return candidate;
        }
    }
    return value;
}

/* The range state: an invalid value becomes the default (the midpoint of min and max, defaulting to 0 and 100), then
   a value under min or over max is clamped and a value off the step grid moves to the nearest step. A valid value the
   constraints leave alone is submitted as written. */
static PyObject *range_value(th_node *input, const Py_UCS4 *text, Py_ssize_t len) {
    double minimum = 0.0;
    double maximum = 100.0;
    double step = 1.0;
    double number = 0.0;
    int min_given = attr_number(input, TH_ATTR_MIN, &minimum);
    int parsed = parse_float_rules(text, len, &number);
    if (min_given < 0 || attr_number(input, TH_ATTR_MAX, &maximum) < 0 || /* GCOVR_EXCL_BR_LINE: OOM only */
        parsed < 0) {                                                     /* GCOVR_EXCL_BR_LINE: OOM only */
        return NULL;                                                      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    /* the step base is min, else the value attribute's number, else zero */
    double base = min_given ? minimum : parsed ? number : 0.0;
    int valid = valid_float(text, len);
    if (valid && !parsed) { /* a valid literal past the double range has no number to constrain */
        return ucs4_to_str(text, len);
    }
    if (!valid) {
        /* halving the difference adds at most one decimal to those of min and max */
        number = maximum < minimum ? minimum
                                   : round_decimals(minimum + (maximum - minimum) / 2,
                                                    larger(fraction_digits(minimum), fraction_digits(maximum)) + 1);
    }
    double constrained = number < minimum ? minimum : maximum >= minimum && number > maximum ? maximum : number;
    const th_node_attr *step_attr = find_node_attr(input, TH_ATTR_STEP);
    int any_step =
        step_attr != NULL && step_attr->value != NULL && ascii_ieq(step_attr->value, step_attr->value_len, "any");
    if (!any_step) {
        int step_given = attr_number(input, TH_ATTR_STEP, &step);
        if (step_given < 0) { /* GCOVR_EXCL_BR_LINE: parse_float_rules fails only on OOM */
            return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        if (!step_given || step <= 0.0) {
            step = 1.0;
        }
        constrained = step_align(constrained, base, step, minimum, maximum);
    }
    return valid && constrained == number ? ucs4_to_str(text, len) : number_to_str(constrained);
}

/* A str from a code-point run, with leading and trailing ASCII whitespace removed when trim is set. */
static PyObject *trimmed_str(const Py_UCS4 *text, Py_ssize_t len, int trim) {
    Py_ssize_t start = 0;
    Py_ssize_t end = len;
    while (trim && start < end && is_space(text[start])) {
        start++;
    }
    while (trim && end > start && is_space(text[end - 1])) {
        end--;
    }
    return ucs4_to_str(text + start, end - start);
}

/* The value with every LF and CR removed (the first at index newline), and with leading and trailing ASCII whitespace
   stripped when trim is set. */
static TH_NOINLINE PyObject *without_newlines(const Py_UCS4 *text, Py_ssize_t len, Py_ssize_t newline, int trim) {
    Py_UCS4 *buffer = PyMem_Malloc((size_t)len * sizeof(Py_UCS4));
    if (buffer == NULL) {        /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    memcpy(buffer, text, (size_t)newline * sizeof(Py_UCS4));
    Py_ssize_t end = newline;
    for (Py_ssize_t index = newline + 1; index < len; index++) {
        if (text[index] != '\n' && text[index] != '\r') {
            buffer[end++] = text[index];
        }
    }
    PyObject *result = trimmed_str(buffer, end, trim);
    PyMem_Free(buffer);
    return result;
}

/* The value with every LF and CR removed, and with leading and trailing ASCII whitespace stripped when trim is set. */
static PyObject *strip_newlines(const Py_UCS4 *text, Py_ssize_t len, int trim) {
    Py_ssize_t newline = 0;
    while (newline < len && text[newline] > '\r') { /* one compare per character skips everything above CR */
        newline++;
    }
    while (newline < len && text[newline] != '\n' && text[newline] != '\r') {
        newline++;
    }
    if (newline == len) { /* most values carry no newline, so they need no copy */
        return trim ? trimmed_str(text, len, 1) : ucs4_to_str(text, len);
    }
    return without_newlines(text, len, newline, trim);
}

/* A multiple email value: split on commas, each token stripped of ASCII whitespace, rejoined with single commas. A
   trailing comma ends the list without adding an empty token. */
static PyObject *email_list_value(const Py_UCS4 *text, Py_ssize_t len) {
    Py_UCS4 *buffer = PyMem_Malloc((size_t)(len > 0 ? len : 1) * sizeof(Py_UCS4));
    if (buffer == NULL) {        /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t used = 0;
    Py_ssize_t pos = 0;
    while (pos < len) {
        Py_ssize_t token_start = pos;
        Py_ssize_t start = pos;
        while (pos < len && text[pos] != ',') {
            pos++;
        }
        Py_ssize_t end = pos;
        while (start < end && is_space(text[start])) {
            start++;
        }
        while (end > start && is_space(text[end - 1])) {
            end--;
        }
        if (token_start > 0) {
            buffer[used++] = ',';
        }
        memcpy(buffer + used, text + start, (size_t)(end - start) * sizeof(Py_UCS4));
        used += end - start;
        pos += pos < len;
    }
    PyObject *result = ucs4_to_str(buffer, used);
    PyMem_Free(buffer);
    return result;
}

/* A value kept when valid for its state's microsyntax and emptied otherwise. */
static PyObject *kept_if(int valid, const Py_UCS4 *text, Py_ssize_t len) {
    return valid ? ucs4_to_str(text, len) : PyUnicode_New(0, 0);
}

/* The value of an input whose type attribute picks its value sanitization state. Kept out of line so the common
   typeless input, a text state, runs in a small function without this switch's register pressure. */
static TH_NOINLINE PyObject *typed_input_value(th_node *input, const th_node_attr *type, const Py_UCS4 *text,
                                               Py_ssize_t len) {
    int cycle = 0;
    int month = 0;
    time_shape shape;
    switch (input_state_of(type)) {
    case STATE_VERBATIM:
        return ucs4_to_str(text, len);
    case STATE_URL:
        return strip_newlines(text, len, 1);
    case STATE_EMAIL:
        return find_node_attr(input, TH_ATTR_MULTIPLE) != NULL ? email_list_value(text, len)
                                                               : strip_newlines(text, len, 1);
    case STATE_NUMBER:
        return kept_if(valid_float(text, len), text, len);
    case STATE_RANGE:
        return range_value(input, text, len);
    case STATE_DATE:
        return kept_if(parse_date_at(text, len, 0) == len, text, len);
    case STATE_MONTH:
        return kept_if(parse_month_at(text, len, 0, &cycle, &month) == len, text, len);
    case STATE_WEEK:
        return kept_if(valid_week(text, len), text, len);
    case STATE_TIME:
        return kept_if(parse_time_at(text, len, 0, &shape) == len, text, len);
    case STATE_DATETIME_LOCAL:
        return datetime_local_value(text, len);
    default: /* STATE_TEXT */
        return strip_newlines(text, len, 0);
    }
}

PyObject *th_form_input_value(th_node *input, const th_node_attr *type, const th_node_attr *value) {
    const Py_UCS4 *text = value != NULL ? value->value : NULL;
    Py_ssize_t len = text != NULL ? value->value_len : 0;
    if (type == NULL || type->value == NULL) {
        return strip_newlines(text, len, 0);
    }
    return typed_input_value(input, type, text, len);
}

PyObject *th_form_textarea_value(th_tree *tree, th_node *textarea) {
    Py_ssize_t len;
    Py_UCS4 *buffer = th_node_text(tree, textarea, &len);
    if (buffer == NULL) {        /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t used = 0;
    for (Py_ssize_t index = 0; index < len; index++) {
        if (buffer[index] == '\r') {
            buffer[used++] = '\n';
            index += index + 1 < len && buffer[index + 1] == '\n';
        } else {
            buffer[used++] = buffer[index];
        }
    }
    PyObject *result = ucs4_to_str(buffer, used);
    PyMem_Free(buffer);
    return result;
}
