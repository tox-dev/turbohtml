#ifndef TURBOHTML_C14N_WRITERS_H
#define TURBOHTML_C14N_WRITERS_H

#include "serialize/internal.h"

/* Keep these writers local so the ordinary C14N path retains its inlining. */
static void c14n_put_text(sbuf *out, const Py_UCS4 *text, Py_ssize_t len) {
    Py_ssize_t index = 0;
    while (index < len) {
        Py_ssize_t start = index;
        while (index < len && text[index] != '&' && text[index] != '<' && text[index] != '>' && text[index] != 0x0D) {
            index++;
        }
        if (index > start) {
            sbuf_put_run(out, &text[start], index - start);
        }
        if (index < len) {
            Py_UCS4 character = text[index];
            if (character == '&') {
                sbuf_puts(out, "&amp;");
            } else if (character == '<') {
                sbuf_puts(out, "&lt;");
            } else if (character == '>') {
                sbuf_puts(out, "&gt;");
            } else {
                sbuf_puts(out, "&#xD;"); /* the only remaining flagged character is U+000D */
            }
            index++;
        }
    }
}

static void c14n_put_attr(sbuf *out, const Py_UCS4 *text, Py_ssize_t len) {
    Py_ssize_t index = 0;
    while (index < len) {
        Py_ssize_t start = index;
        while (index < len && text[index] != '&' && text[index] != '<' && text[index] != '"' && text[index] != 0x09 &&
               text[index] != 0x0A && text[index] != 0x0D) {
            index++;
        }
        if (index > start) {
            sbuf_put_run(out, &text[start], index - start);
        }
        if (index < len) {
            Py_UCS4 character = text[index];
            if (character == '&') {
                sbuf_puts(out, "&amp;");
            } else if (character == '<') {
                sbuf_puts(out, "&lt;");
            } else if (character == '"') {
                sbuf_puts(out, "&quot;");
            } else if (character == 0x09) {
                sbuf_puts(out, "&#x9;");
            } else if (character == 0x0A) {
                sbuf_puts(out, "&#xA;");
            } else {
                sbuf_puts(out, "&#xD;"); /* the only remaining flagged character is U+000D */
            }
            index++;
        }
    }
}

static void c14n_close_tag(sbuf *out, const th_node *node) {
    sbuf_puts(out, "</");
    sbuf_put_ucs4(out, node->text, node->text_len);
    sbuf_putc(out, '>');
}

static void c14n_put_pi(sbuf *out, const th_node *node) {
    Py_ssize_t target_len = node->attr_count;
    sbuf_puts(out, "<?");
    sbuf_put_ucs4(out, node->text, target_len);
    Py_ssize_t data_len = node->text_len - target_len - 1;
    if (data_len > 0) {
        sbuf_putc(out, ' ');
        sbuf_put_xml_pi_data(out, node->text + target_len + 1, data_len);
    }
    sbuf_puts(out, "?>");
}

#endif
