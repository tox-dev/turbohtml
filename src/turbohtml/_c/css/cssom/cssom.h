#ifndef TURBOHTML_CSSOM_H
#define TURBOHTML_CSSOM_H

#include "core/common.h"

#include <stddef.h>

enum th_whitespace { TH_WS_NORMAL, TH_WS_LINES, TH_WS_PRESERVE };

int th_css_inline_whitespace(const Py_UCS4 *source, Py_ssize_t length, int inherited);
size_t th_css_deep_memo_limit(size_t rules, size_t nodes);

#endif
