/* Shared character-reference tables and lookups.

   The HTML5 named-reference table and the numeric-reference correction tables
   are consulted from two places: unescape() resolves every reference in a
   string, and the tokenizer resolves them inside its character-reference
   state. Both go through the accessors declared here so the generated tables
   in data/html_entities.h have a single owner (tokenizer/charref.c) rather than being
   compiled into each consumer. The scanning policy (how far to read, when a
   reference is "in an attribute", which parse errors to report) stays with the
   consumer; this file only answers table questions. */

#ifndef TURBOHTML_CHARREF_H
#define TURBOHTML_CHARREF_H

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include "data/html_entities.h" /* html5_entity, HTML5_MAX_NAME_LEN */

/* Binary search the named-reference table for the exact ASCII name. The
   trailing ';' is part of the name for entities that require it, so callers
   pass it as part of name/len when present. Returns NULL when nothing matches. */
const html5_entity *charref_find_entity(const char *name, Py_ssize_t len);

/* Resolve the Windows-1252 / spec correction for a numeric reference. Returns
   1 and sets *replacement when num has an override, 0 otherwise. */
int charref_find_invalid(Py_UCS4 num, Py_UCS4 *replacement);

/* Report whether num is one of the spec's control/noncharacter code points
   that a numeric reference maps to the empty string. */
int charref_is_invalid_codepoint(Py_UCS4 num);

/* Hex-digit value for character, or -1 when it is not a hex digit. */
int charref_hex_value(Py_UCS4 character);

#endif /* TURBOHTML_CHARREF_H */
