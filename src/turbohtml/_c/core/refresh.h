/* The HTML "shared declarative refresh steps" for a <meta http-equiv=refresh> content value. Every reader of that value
   (the link scanner and Document.meta_refresh) calls this one routine, so they cannot disagree about which text a
   browser follows. Python-free, like core/ascii.h, so the link scanner and the document reader both compile it. */

#ifndef TURBOHTML_CORE_REFRESH_H
#define TURBOHTML_CORE_REFRESH_H

#include "core/ascii.h"

#include <stddef.h>
#include <stdint.h>

typedef struct {
    ptrdiff_t delay_start;
    ptrdiff_t delay_end;
    ptrdiff_t target_start;
    ptrdiff_t target_end;
} refresh_span;

static inline ptrdiff_t refresh_skip_space(const uint32_t *value, ptrdiff_t pos, ptrdiff_t len) {
    while (pos < len && is_space(value[pos])) {
        pos++;
    }
    return pos;
}

/* Returns 0 when the value is not a refresh directive: no leading time, or a character right after the time that is
   neither ';', ',' nor whitespace. Otherwise fills `span`; the target is empty when the directive names none. The delay
   is digits with at most one fraction, and a lone '.' is a delay the caller reads as zero. The "url=" keyword counts
   only right after the separator. A quote after the keyword or the separator runs to its match, or to the end of the
   value. */
static inline int refresh_read(const uint32_t *value, ptrdiff_t len, refresh_span *span) {
    ptrdiff_t pos = refresh_skip_space(value, 0, len);
    span->delay_start = pos;
    while (pos < len && is_ascii_digit(value[pos])) {
        pos++;
    }
    if (pos < len && value[pos] == '.') {
        pos++;
        while (pos < len && is_ascii_digit(value[pos])) {
            pos++;
        }
    }
    if (pos == span->delay_start) {
        return 0;
    }
    span->delay_end = pos;
    while (pos < len && (is_ascii_digit(value[pos]) || value[pos] == '.')) {
        pos++;
    }
    if (pos < len) {
        if (value[pos] != ';' && value[pos] != ',' && !is_space(value[pos])) {
            return 0;
        }
        pos = refresh_skip_space(value, pos, len);
        if (pos < len && (value[pos] == ';' || value[pos] == ',')) {
            pos++;
        }
        pos = refresh_skip_space(value, pos, len);
    }
    ptrdiff_t start = pos;
    ptrdiff_t end = len;
    if (starts_with_ascii_ci(value + pos, len - pos, "url")) {
        ptrdiff_t after = refresh_skip_space(value, pos + 3, len);
        if (after < len && value[after] == '=') {
            start = refresh_skip_space(value, after + 1, len);
        }
    }
    if (start < len && (value[start] == '"' || value[start] == '\'')) {
        uint32_t quote = value[start++];
        end = start;
        while (end < len && value[end] != quote) {
            end++;
        }
    }
    span->target_start = refresh_skip_space(value, start, end);
    span->target_end = end;
    while (span->target_end > span->target_start && is_space(value[span->target_end - 1])) {
        span->target_end--;
    }
    return 1;
}

#endif /* TURBOHTML_CORE_REFRESH_H */
