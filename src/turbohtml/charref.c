/* Character-reference table lookups shared by unescape() and the tokenizer.

   These are pure table queries over the generated arrays in html_entities.h:
   a binary search over the sorted named-reference table and two binary
   searches over the numeric-correction tables. They hold no state and never
   touch Python objects. */

#include "charref.h"

#include <string.h>

static int cmp_name(const char *left, Py_ssize_t left_len, const char *right, unsigned right_len) {
    Py_ssize_t shared = left_len < (Py_ssize_t)right_len ? left_len : (Py_ssize_t)right_len;
    int order = memcmp(left, right, (size_t)shared);
    if (order != 0) {
        return order < 0 ? -1 : 1;
    }
    if (left_len == (Py_ssize_t)right_len) {
        return 0;
    }
    return left_len < (Py_ssize_t)right_len ? -1 : 1;
}

const html5_entity *charref_find_entity(const char *name, Py_ssize_t len) {
    int low = 0, high = html5_count - 1;
    while (low <= high) {
        int mid = (low + high) >> 1;
        const html5_entity *entity = &html5_entities[mid];
        int order = cmp_name(name, len, entity->name, entity->name_len);
        if (order == 0) {
            return entity;
        }
        if (order < 0) {
            high = mid - 1;
        } else {
            low = mid + 1;
        }
    }
    return NULL;
}

int charref_find_invalid(Py_UCS4 num, Py_UCS4 *replacement) {
    int low = 0, high = invalid_charref_count - 1;
    while (low <= high) {
        int mid = (low + high) >> 1;
        Py_UCS4 candidate = invalid_charrefs[mid].num;
        if (candidate == num) {
            *replacement = invalid_charrefs[mid].cp;
            return 1;
        }
        if (num < candidate) {
            high = mid - 1;
        } else {
            low = mid + 1;
        }
    }
    return 0;
}

int charref_is_invalid_codepoint(Py_UCS4 num) {
    int low = 0, high = invalid_codepoint_count - 1;
    while (low <= high) {
        int mid = (low + high) >> 1;
        Py_UCS4 candidate = invalid_codepoints[mid];
        if (candidate == num) {
            return 1;
        }
        if (num < candidate) {
            high = mid - 1;
        } else {
            low = mid + 1;
        }
    }
    return 0;
}

int charref_hex_value(Py_UCS4 character) {
    if (character >= '0' && character <= '9')
        return (int)(character - '0');
    if (character >= 'a' && character <= 'f')
        return (int)(character - 'a') + 10;
    if (character >= 'A' && character <= 'F')
        return (int)(character - 'A') + 10;
    return -1;
}
