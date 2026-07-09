/* The WHATWG Encoding Standard's decoders (https://encoding.spec.whatwg.org/, sections 9 through 14), #included into
   dom/document.c after encoding/encoding.h.

   These used to delegate to the CPython codec of the same name, which is wrong in two independent ways. The tables
   differ: the spec's koi8-u is KOI8-RU, its Big5 index is a superset of CPython's, its EUC-KR is windows-949, its
   Shift_JIS is windows-31j, its gb18030 is the 2005 revision, and its single-byte indexes map the unassigned
   0x80..0x9F bytes to the matching C1 control where every CPython codec raises. And the error handling differs: the
   spec pushes an ASCII trail byte back onto the stream, so Big5 "81 41" is one U+FFFD followed by "A", while a
   non-ASCII trail byte is consumed, so "81 FF" is one U+FFFD and not two. No CPython codec reproduces that, so no
   rename could have fixed this.

   UTF-8 and the two UTF-16 forms still delegate: CPython's decoders agree with the spec byte for byte, including
   emitting one U+FFFD per maximal subpart.

   A decoder is a th_decoder stepped by th_decode_step, which yields one code point, an error, or end of stream.
   th_decode drives it into a str, mapping every error to U+FFFD as the spec's "decode" entry point does; detect.h
   drives it in strict mode, where the first error disqualifies a candidate encoding. */

#ifndef TURBOHTML_ENCODING_DECODE_H
#define TURBOHTML_ENCODING_DECODE_H

#include "encoding/encoding.h"

#include <stdint.h>

/* A decoder is called once per code point, and the call costs more than the decoding: forcing it into the specialized
   loop th_decode_chunk builds per encoding is worth about 1.4x on Shift_JIS and Big5. The coverage build compiles at
   -O0, where an inlined copy still lands in every call site and gcc counts its branches once per copy, so leave the
   decoders standing there -- the gate measures reachability, not speed. */
#if defined(_MSC_VER)
#define TH_HOT __forceinline
#elif defined(__OPTIMIZE__)
#define TH_HOT inline __attribute__((always_inline))
#else
#define TH_HOT
#endif

#define TH_DEC_EOF (-1)
#define TH_DEC_ERROR (-1)
#define TH_DEC_FINISHED 0
#define TH_DEC_POINT 1

/* The spec's four Big5 pointers that decode to a base letter plus a combining mark; every other pointer yields one
   code point. The first two pair with U+00CA, the last two with U+00EA. */
#define TH_BIG5_COMBINING_MACRON_CAPITAL 1133
#define TH_BIG5_COMBINING_CARON_CAPITAL 1135
#define TH_BIG5_COMBINING_MACRON_SMALL 1164
#define TH_BIG5_COMBINING_CARON_SMALL 1166

/* ISO-2022-JP decoder states, in the spec's order. output_state remembers the last of ASCII, Roman, and Katakana so an
   incomplete escape sequence can fall back to it. */
typedef enum {
    TH_JP_ASCII,
    TH_JP_ROMAN,
    TH_JP_KATAKANA,
    TH_JP_LEAD,
    TH_JP_TRAIL,
    TH_JP_ESCAPE_START,
    TH_JP_ESCAPE,
} th_jp_state;

typedef struct {
    const unsigned char *buf;
    Py_ssize_t len;
    Py_ssize_t pos;
    uint8_t kind;   /* th_dec_kind */
    uint8_t single; /* the th_sb_index row, when kind is TH_DEC_SINGLE_BYTE */
    unsigned char lead;
    unsigned char first; /* gb18030 keeps three pending bytes, every other decoder keeps one */
    unsigned char second;
    unsigned char third;
    unsigned char jis0212; /* the pending EUC-JP pair indexes jis0212 rather than jis0208 */
    unsigned char state;
    unsigned char output_state;
    unsigned char output_flag;
    Py_UCS4 pending; /* the combining mark a Big5 combination still owes the caller */
    unsigned char has_pending;
    /* The offset of the byte that triggered the last TH_DEC_ERROR, so the detector can recover chardetng's
       byte-at-a-time `b` (buf[error_at]) and `prev_byte` (buf[error_at - 1]). Set to pos - 1 before any pushback
       rewind; a value of -1 marks an end-of-stream flush of an incomplete sequence, which chardetng scores as death. */
    Py_ssize_t error_at;
} th_decoder;

static void th_decode_init(th_decoder *dec, const th_encoding_entry *entry, const unsigned char *buf, Py_ssize_t len) {
    memset(dec, 0, sizeof(*dec)); /* TH_JP_ASCII is zero, so this also seeds the ISO-2022-JP states */
    dec->buf = buf;
    dec->len = len;
    dec->kind = entry->kind;
    dec->single = entry->single;
}

/* The next byte, or TH_DEC_EOF past the end. An error path rewinds pos to "prepend" the bytes it did not consume. */
static TH_HOT int th_dec_next(th_decoder *dec) {
    return dec->pos < dec->len ? dec->buf[dec->pos++] : TH_DEC_EOF;
}

/* Each decoder derives a pointer from byte ranges it has already tested, and the largest pointer those ranges allow is
   the last slot of the table it indexes. The bound is therefore a property of the tables, proved once here, rather than
   a branch taken for every code point; the compiler rechecks it whenever the generator resizes a table. */
#define TH_ENTRIES(table) ((int)(sizeof(table) / sizeof((table)[0])))
_Static_assert((0xFE - 0x81) * 157 + (0xFE - 0x62) - TH_BIG5_FIRST < TH_ENTRIES(th_big5_low), "big5 pointer overruns");
_Static_assert((0xFE - 0x81) * 190 + (0xFE - 0x41) < TH_ENTRIES(th_euc_kr), "euc-kr pointer overruns");
_Static_assert((0xFC - 0xC1) * 188 + (0xFC - 0x41) < TH_ENTRIES(th_jis0208), "shift_jis pointer overruns");
_Static_assert((0xFE - 0xA1) * 94 + (0xFE - 0xA1) < TH_ENTRIES(th_jis0212), "euc-jp jis0212 pointer overruns");
_Static_assert((0xFE - 0xA1) * 94 + (0xFE - 0xA1) < TH_ENTRIES(th_jis0208), "euc-jp jis0208 pointer overruns");
_Static_assert((0xFE - 0x81) * 190 + (0xFE - 0x41) < TH_ENTRIES(th_gb18030), "gb18030 pointer overruns");
_Static_assert((0x7E - 0x21) * 94 + (0x7E - 0x21) < TH_ENTRIES(th_jis0208), "iso-2022-jp pointer overruns");

static TH_HOT int th_dec_ascii(int byte) {
    return byte >= 0x00 && byte <= 0x7F;
}

/* 0x80..0xFF map to the private-use block U+F780..U+F7FF. ASCII stays put, and never arrives: this is the one decoder
   nothing but th_decode_run drives, and the run copies each ASCII byte out before it steps. */
static TH_HOT int th_dec_x_user_defined(th_decoder *dec, Py_UCS4 *point) {
    int byte = th_dec_next(dec);
    if (byte == TH_DEC_EOF) {
        return TH_DEC_FINISHED;
    }
    *point = (Py_UCS4)(0xF700 + byte);
    return TH_DEC_POINT;
}

static TH_HOT int th_dec_big5(th_decoder *dec, Py_UCS4 *point) {
    if (dec->has_pending) {
        dec->has_pending = 0;
        *point = dec->pending;
        return TH_DEC_POINT;
    }
    for (;;) {
        int byte = th_dec_next(dec);
        if (dec->lead != 0) {
            unsigned char lead = dec->lead;
            dec->lead = 0;
            if (byte != TH_DEC_EOF) {
                int offset = byte < 0x7F ? 0x40 : 0x62;
                if ((byte >= 0x40 && byte <= 0x7E) || (byte >= 0xA1 && byte <= 0xFE)) {
                    int pointer = (lead - 0x81) * 157 + (byte - offset);
                    if (pointer == TH_BIG5_COMBINING_MACRON_CAPITAL || pointer == TH_BIG5_COMBINING_CARON_CAPITAL) {
                        dec->pending = pointer == TH_BIG5_COMBINING_MACRON_CAPITAL ? 0x0304 : 0x030C;
                        dec->has_pending = 1;
                        *point = 0x00CA;
                        return TH_DEC_POINT;
                    }
                    if (pointer == TH_BIG5_COMBINING_MACRON_SMALL || pointer == TH_BIG5_COMBINING_CARON_SMALL) {
                        dec->pending = pointer == TH_BIG5_COMBINING_MACRON_SMALL ? 0x0304 : 0x030C;
                        dec->has_pending = 1;
                        *point = 0x00EA;
                        return TH_DEC_POINT;
                    }
                    int rebased = pointer - TH_BIG5_FIRST;
                    if (rebased >= 0 && th_big5_low[rebased] != 0) {
                        uint16_t low = th_big5_low[rebased];
                        int astral = (th_big5_astral[rebased >> 3] >> (rebased & 7)) & 1;
                        *point = astral ? (TH_BIG5_PLANE | low) : low;
                        return TH_DEC_POINT;
                    }
                }
                dec->error_at = dec->pos - 1;
                if (th_dec_ascii(byte)) { /* an ASCII trail byte is pushed back and decodes on its own */
                    dec->pos--;
                }
            } else {
                dec->error_at = -1; /* a trailing lead byte with no trail: an end-of-stream flush */
            }
            return TH_DEC_ERROR;
        }
        if (byte == TH_DEC_EOF) {
            return TH_DEC_FINISHED;
        }
        if (th_dec_ascii(byte)) {
            *point = (Py_UCS4)byte;
            return TH_DEC_POINT;
        }
        if (byte >= 0x81 && byte <= 0xFE) {
            dec->lead = (unsigned char)byte;
            continue;
        }
        dec->error_at = dec->pos - 1;
        return TH_DEC_ERROR;
    }
}

static TH_HOT int th_dec_euc_kr(th_decoder *dec, Py_UCS4 *point) {
    for (;;) {
        int byte = th_dec_next(dec);
        if (dec->lead != 0) {
            unsigned char lead = dec->lead;
            dec->lead = 0;
            if (byte >= 0x41 && byte <= 0xFE) {
                int pointer = (lead - 0x81) * 190 + (byte - 0x41);
                if (th_euc_kr[pointer] != 0) {
                    *point = th_euc_kr[pointer];
                    return TH_DEC_POINT;
                }
            }
            dec->error_at = byte == TH_DEC_EOF ? -1 : dec->pos - 1;
            if (th_dec_ascii(byte)) {
                dec->pos--;
            }
            return TH_DEC_ERROR;
        }
        if (byte == TH_DEC_EOF) {
            return TH_DEC_FINISHED;
        }
        if (th_dec_ascii(byte)) {
            *point = (Py_UCS4)byte;
            return TH_DEC_POINT;
        }
        if (byte >= 0x81 && byte <= 0xFE) {
            dec->lead = (unsigned char)byte;
            continue;
        }
        dec->error_at = dec->pos - 1;
        return TH_DEC_ERROR;
    }
}

static TH_HOT int th_dec_shift_jis(th_decoder *dec, Py_UCS4 *point) {
    for (;;) {
        int byte = th_dec_next(dec);
        if (dec->lead != 0) {
            unsigned char lead = dec->lead;
            dec->lead = 0;
            if (byte != TH_DEC_EOF) {
                int offset = byte < 0x7F ? 0x40 : 0x41;
                int lead_offset = lead < 0xA0 ? 0x81 : 0xC1;
                if ((byte >= 0x40 && byte <= 0x7E) || (byte >= 0x80 && byte <= 0xFC)) {
                    int pointer = (lead - lead_offset) * 188 + (byte - offset);
                    /* a private-use run the spec computes rather than tables */
                    if (pointer >= 8836 && pointer <= 10715) {
                        *point = (Py_UCS4)(0xE000 - 8836 + pointer);
                        return TH_DEC_POINT;
                    }
                    if (th_jis0208[pointer] != 0) {
                        *point = th_jis0208[pointer];
                        return TH_DEC_POINT;
                    }
                }
                dec->error_at = dec->pos - 1;
                if (th_dec_ascii(byte)) {
                    dec->pos--;
                }
            } else {
                dec->error_at = -1;
            }
            return TH_DEC_ERROR;
        }
        if (byte == TH_DEC_EOF) {
            return TH_DEC_FINISHED;
        }
        if (byte <= 0x80) { /* 0x80 is not an ASCII byte, yet the spec passes it through unchanged */
            *point = (Py_UCS4)byte;
            return TH_DEC_POINT;
        }
        if (byte >= 0xA1 && byte <= 0xDF) {
            *point = (Py_UCS4)(0xFF61 - 0xA1 + byte);
            return TH_DEC_POINT;
        }
        if (byte <= 0x9F || (byte >= 0xE0 && byte <= 0xFC)) { /* 0x80 and the katakana run already returned */
            dec->lead = (unsigned char)byte;
            continue;
        }
        dec->error_at = dec->pos - 1;
        return TH_DEC_ERROR;
    }
}

static TH_HOT int th_dec_euc_jp(th_decoder *dec, Py_UCS4 *point) {
    for (;;) {
        int byte = th_dec_next(dec);
        if (dec->lead == 0x8E && byte >= 0xA1 && byte <= 0xDF) {
            dec->lead = 0;
            *point = (Py_UCS4)(0xFF61 - 0xA1 + byte);
            return TH_DEC_POINT;
        }
        if (dec->lead == 0x8F && byte >= 0xA1 && byte <= 0xFE) {
            dec->jis0212 = 1;
            dec->lead = (unsigned char)byte;
            continue;
        }
        if (dec->lead != 0) {
            unsigned char lead = dec->lead;
            unsigned char from_jis0212 = dec->jis0212;
            dec->lead = 0;
            dec->jis0212 = 0;
            if (lead >= 0xA1 && byte >= 0xA1 && byte <= 0xFE) { /* lead holds only 0x8E, 0x8F, or 0xA1..0xFE */
                int pointer = (lead - 0xA1) * 94 + (byte - 0xA1);
                const uint16_t *table = from_jis0212 ? th_jis0212 : th_jis0208;
                if (table[pointer] != 0) {
                    *point = table[pointer];
                    return TH_DEC_POINT;
                }
            }
            dec->error_at = byte == TH_DEC_EOF ? -1 : dec->pos - 1;
            if (th_dec_ascii(byte)) {
                dec->pos--;
            }
            return TH_DEC_ERROR;
        }
        if (byte == TH_DEC_EOF) {
            return TH_DEC_FINISHED;
        }
        if (th_dec_ascii(byte)) {
            *point = (Py_UCS4)byte;
            return TH_DEC_POINT;
        }
        if (byte == 0x8E || byte == 0x8F || (byte >= 0xA1 && byte <= 0xFE)) {
            dec->lead = (unsigned char)byte;
            continue;
        }
        dec->error_at = dec->pos - 1;
        return TH_DEC_ERROR;
    }
}

/* The spec's "index gb18030 ranges code point", a binary search over the sorted range starts. */
static int th_dec_gb18030_range(uint32_t pointer, Py_UCS4 *point) {
    if ((pointer > 39419 && pointer < 189000) || pointer > 1237575) {
        return 0;
    }
    if (pointer == 7457) { /* the one pointer the range list cannot express */
        *point = 0xE7C7;
        return 1;
    }
    size_t low = 0;
    size_t high = sizeof(th_gb18030_ranges) / sizeof(th_gb18030_ranges[0]);
    while (low + 1 < high) {
        size_t mid = low + (high - low) / 2;
        if (th_gb18030_ranges[mid].pointer <= pointer) {
            low = mid;
        } else {
            high = mid;
        }
    }
    *point = th_gb18030_ranges[low].code_point + (pointer - th_gb18030_ranges[low].pointer);
    return 1;
}

static TH_HOT int th_dec_gb18030(th_decoder *dec, Py_UCS4 *point) {
    for (;;) {
        int byte = th_dec_next(dec);
        if (byte == TH_DEC_EOF) {
            if (dec->first == 0) { /* second is set only once first is, and third only once second is */
                return TH_DEC_FINISHED;
            }
            dec->first = dec->second = dec->third = 0;
            dec->error_at = -1;
            return TH_DEC_ERROR;
        }
        if (dec->third != 0) {
            if (byte < 0x30 || byte > 0x39) { /* the spec prepends all three pending bytes, not just the last */
                dec->error_at = dec->pos - 1;
                dec->pos -= 3;
                dec->first = dec->second = dec->third = 0;
                return TH_DEC_ERROR;
            }
            uint32_t pointer = ((uint32_t)(dec->first - 0x81) * 10 * 126 * 10) +
                               ((uint32_t)(dec->second - 0x30) * 10 * 126) + ((uint32_t)(dec->third - 0x81) * 10) +
                               (uint32_t)(byte - 0x30);
            dec->first = dec->second = dec->third = 0;
            return th_dec_gb18030_range(pointer, point) ? TH_DEC_POINT : TH_DEC_ERROR;
        }
        if (dec->second != 0) {
            if (byte >= 0x81 && byte <= 0xFE) {
                dec->third = (unsigned char)byte;
                continue;
            }
            dec->error_at = dec->pos - 1;
            dec->pos -= 2;
            dec->first = dec->second = 0;
            return TH_DEC_ERROR;
        }
        if (dec->first != 0) {
            if (byte >= 0x30 && byte <= 0x39) {
                dec->second = (unsigned char)byte;
                continue;
            }
            unsigned char lead = dec->first;
            dec->first = 0;
            int offset = byte < 0x7F ? 0x40 : 0x41;
            if ((byte >= 0x40 && byte <= 0x7E) || (byte >= 0x80 && byte <= 0xFE)) {
                /* every two-byte pointer maps; the generator refuses to emit a gb18030 table with a hole */
                *point = th_gb18030[(lead - 0x81) * 190 + (byte - offset)];
                return TH_DEC_POINT;
            }
            dec->error_at = dec->pos - 1;
            if (th_dec_ascii(byte)) {
                dec->pos--;
            }
            return TH_DEC_ERROR;
        }
        if (th_dec_ascii(byte)) {
            *point = (Py_UCS4)byte;
            return TH_DEC_POINT;
        }
        if (byte == 0x80) { /* GBK's euro sign, which CPython's gb18030 rejects outright */
            *point = 0x20AC;
            return TH_DEC_POINT;
        }
        if (byte <= 0xFE) { /* the ASCII bytes and 0x80 already returned */
            dec->first = (unsigned char)byte;
            continue;
        }
        dec->error_at = dec->pos - 1;
        return TH_DEC_ERROR;
    }
}

static TH_HOT int th_dec_iso_2022_jp(th_decoder *dec, Py_UCS4 *point) {
    for (;;) {
        int byte = th_dec_next(dec);
        switch (dec->state) {
        case TH_JP_ASCII:
            if (byte == 0x1B) {
                dec->state = TH_JP_ESCAPE_START;
                continue;
            }
            if (byte == TH_DEC_EOF) {
                return TH_DEC_FINISHED;
            }
            dec->output_flag = 0;
            if (byte <= 0x7F && byte != 0x0E && byte != 0x0F) {
                *point = (Py_UCS4)byte;
                return TH_DEC_POINT;
            }
            dec->error_at = dec->pos - 1;
            return TH_DEC_ERROR;
        case TH_JP_ROMAN:
            if (byte == 0x1B) {
                dec->state = TH_JP_ESCAPE_START;
                continue;
            }
            if (byte == TH_DEC_EOF) {
                return TH_DEC_FINISHED;
            }
            dec->output_flag = 0;
            if (byte == 0x5C) { /* Roman is ASCII with the backslash and tilde replaced, and nothing else */
                *point = 0x00A5;
                return TH_DEC_POINT;
            }
            if (byte == 0x7E) {
                *point = 0x203E;
                return TH_DEC_POINT;
            }
            if (byte <= 0x7F && byte != 0x0E && byte != 0x0F) {
                *point = (Py_UCS4)byte;
                return TH_DEC_POINT;
            }
            dec->error_at = dec->pos - 1;
            return TH_DEC_ERROR;
        case TH_JP_KATAKANA: /* the half-width katakana state CPython's iso2022_jp codec does not implement */
            if (byte == 0x1B) {
                dec->state = TH_JP_ESCAPE_START;
                continue;
            }
            if (byte == TH_DEC_EOF) {
                return TH_DEC_FINISHED;
            }
            dec->output_flag = 0;
            if (byte >= 0x21 && byte <= 0x5F) {
                *point = (Py_UCS4)(0xFF61 - 0x21 + byte);
                return TH_DEC_POINT;
            }
            dec->error_at = dec->pos - 1;
            return TH_DEC_ERROR;
        case TH_JP_LEAD:
            if (byte == 0x1B) {
                dec->state = TH_JP_ESCAPE_START;
                continue;
            }
            if (byte == TH_DEC_EOF) {
                return TH_DEC_FINISHED;
            }
            dec->output_flag = 0;
            if (byte >= 0x21 && byte <= 0x7E) {
                dec->lead = (unsigned char)byte;
                dec->state = TH_JP_TRAIL;
                continue;
            }
            dec->error_at = dec->pos - 1;
            return TH_DEC_ERROR;
        case TH_JP_TRAIL:
            if (byte == 0x1B) {
                dec->state = TH_JP_ESCAPE_START;
                dec->error_at = dec->pos - 1;
                return TH_DEC_ERROR;
            }
            dec->state = TH_JP_LEAD;
            if (byte >= 0x21 && byte <= 0x7E) {
                int pointer = (dec->lead - 0x21) * 94 + (byte - 0x21);
                if (th_jis0208[pointer] != 0) {
                    *point = th_jis0208[pointer];
                    return TH_DEC_POINT;
                }
            }
            dec->error_at = byte == TH_DEC_EOF ? -1 : dec->pos - 1;
            return TH_DEC_ERROR;
        case TH_JP_ESCAPE_START:
            if (byte == 0x24 || byte == 0x28) {
                dec->lead = (unsigned char)byte;
                dec->state = TH_JP_ESCAPE;
                continue;
            }
            dec->error_at = byte == TH_DEC_EOF ? -1 : dec->pos - 1;
            if (byte != TH_DEC_EOF) {
                dec->pos--;
            }
            dec->output_flag = 0;
            dec->state = dec->output_state;
            return TH_DEC_ERROR;
        default: { /* TH_JP_ESCAPE */
            unsigned char lead = dec->lead;
            dec->lead = 0;
            int next = -1;
            if (lead == 0x28 && byte == 0x42) {
                next = TH_JP_ASCII;
            } else if (lead == 0x28 && byte == 0x4A) {
                next = TH_JP_ROMAN;
            } else if (lead == 0x28 && byte == 0x49) {
                next = TH_JP_KATAKANA;
            } else if (lead == 0x24 && (byte == 0x40 || byte == 0x42)) {
                next = TH_JP_LEAD;
            }
            if (next >= 0) {
                dec->state = (unsigned char)next;
                dec->output_state = (unsigned char)next;
                int repeated = dec->output_flag; /* back-to-back escapes emit one error between them */
                dec->output_flag = 1;
                if (repeated) {
                    dec->error_at = dec->pos - 1;
                    return TH_DEC_ERROR;
                }
                continue;
            }
            dec->error_at = byte == TH_DEC_EOF ? -1 : dec->pos - 1;
            dec->pos -= byte == TH_DEC_EOF ? 1 : 2; /* the spec prepends the escape's bytes; EOF prepends nothing */
            dec->output_flag = 0;
            dec->state = dec->output_state;
            return TH_DEC_ERROR;
        }
        }
    }
}

typedef int (*th_decode_fn)(th_decoder *dec, Py_UCS4 *point);

/* The offset of the first byte that is not ASCII, or len when every byte is. */
static TH_HOT Py_ssize_t th_decode_ascii_run(const unsigned char *bytes, Py_ssize_t len) {
    Py_ssize_t index = 0;
    while (index + (Py_ssize_t)sizeof(uint64_t) <= len) {
        uint64_t word;
        memcpy(&word, bytes + index, sizeof(word));
        if (word & UINT64_C(0x8080808080808080)) {
            break;
        }
        index += (Py_ssize_t)sizeof(uint64_t);
    }
    while (index < len && bytes[index] < 0x80) {
        index++;
    }
    return index;
}

/* Copy the ASCII run at dec->pos straight out, and return how many code points that was. Every legacy encoding but
   ISO-2022-JP, whose escapes reinterpret an ASCII byte, decodes ASCII to itself, and markup is mostly ASCII even when
   the text around it is not. A decoder clears its lead before it returns, so the only state that can outlive a step is
   the combining mark a Big5 combination still owes the caller, and that mark has to go out before this run does.

   Test the byte before setting up the scan over it: text runs many code points with no ASCII between them, and paying
   for a word-at-a-time scan that finds nothing after each one costs a Shift_JIS page more than copying its markup
   saves. */
static TH_HOT Py_ssize_t th_decode_ascii_copy(th_decoder *dec, Py_UCS4 *out) {
    if (dec->pos >= dec->len || dec->buf[dec->pos] >= 0x80 || dec->has_pending) {
        return 0;
    }
    Py_ssize_t run = th_decode_ascii_run(dec->buf + dec->pos, dec->len - dec->pos);
    for (Py_ssize_t index = 0; index < run; index++) {
        out[index] = dec->buf[dec->pos + index];
    }
    dec->pos += run;
    return run;
}

/* Yield the next code point, TH_DEC_FINISHED at end of stream, or TH_DEC_ERROR for one malformed sequence. The caller
   resumes stepping after an error, which is how a pushed-back ASCII byte reaches the output. Only the encodings the
   content detector scores reach this: a single-byte or x-user-defined stream is decoded in one pass instead. */
static int th_decode_step(th_decoder *dec, Py_UCS4 *point) {
    switch (dec->kind) {
    case TH_DEC_BIG5:
        return th_dec_big5(dec, point);
    case TH_DEC_EUC_KR:
        return th_dec_euc_kr(dec, point);
    case TH_DEC_SHIFT_JIS:
        return th_dec_shift_jis(dec, point);
    case TH_DEC_EUC_JP:
        return th_dec_euc_jp(dec, point);
    case TH_DEC_GB18030:
        return th_dec_gb18030(dec, point);
    default: /* TH_DEC_ISO_2022_JP */
        return th_dec_iso_2022_jp(dec, point);
    }
}

/* Decode a chunk of a byte stream. Unlike th_decode, which sees the whole input, this holds back the trailing bytes of
   a sequence that a later chunk may complete: those are the errors the decoders raise at end of stream, which they
   mark with error_at == -1. A malformed byte errors with an offset instead, and its U+FFFD goes out at once. The
   decoder's state travels with it, since ISO-2022-JP's mode survives a chunk boundary while its bytes do not.

   Returns the number of code points written to out (which must hold len of them), sets *consumed to the offset the next
   chunk should start at, and leaves the mode state in *dec. step is passed as a constant from th_decode_chunk so the
   compiler specializes this body per encoding; an indirect call for every code point costs more than the decoding.
   stateful is a constant too, and marks ISO-2022-JP: the one decoder whose escapes reinterpret an ASCII byte, and so
   the one whose ASCII cannot be copied past it and whose mode has to survive a rewind. */
static inline Py_ssize_t th_decode_run(th_decoder *dec, Py_UCS4 *out, int final, Py_ssize_t *consumed, Py_UCS4 *maxchar,
                                       th_decode_fn step, int stateful) {
    /* Copied once so the guarded update below cannot read an uninitialized decoder; a final chunk never rewinds. */
    th_decoder rewind = *dec;
    Py_ssize_t count = 0;
    for (;;) {
        if (!stateful) {
            count += th_decode_ascii_copy(dec, out + count);
        }
        if (stateful && !final) {
            rewind = *dec;
        }
        Py_ssize_t start = dec->pos;
        Py_UCS4 point = 0;
        int status = step(dec, &point);
        if (status == TH_DEC_FINISHED) {
            *consumed = dec->len;
            return count;
        }
        if (status == TH_DEC_ERROR && !final && dec->error_at == -1) {
            /* An incomplete tail: the next chunk re-reads these bytes. A stateless decoder clears its lead before it
               returns, so the position is the whole of what it left behind; ISO-2022-JP instead falls back to
               output_state, or to the lead state, and the mode those bytes belong to has to come back with them. */
            if (stateful) {
                *dec = rewind;
            }
            *consumed = start;
            return count;
        }
        point = status == TH_DEC_ERROR ? 0xFFFD : point;
        out[count++] = point;
        *maxchar = point > *maxchar ? point : *maxchar;
    }
}

static Py_ssize_t th_decode_chunk(th_decoder *dec, Py_UCS4 *out, int final, Py_ssize_t *consumed, Py_UCS4 *maxchar) {
    switch (dec->kind) {
    case TH_DEC_X_USER_DEFINED:
        return th_decode_run(dec, out, final, consumed, maxchar, th_dec_x_user_defined, 0);
    case TH_DEC_BIG5:
        return th_decode_run(dec, out, final, consumed, maxchar, th_dec_big5, 0);
    case TH_DEC_EUC_KR:
        return th_decode_run(dec, out, final, consumed, maxchar, th_dec_euc_kr, 0);
    case TH_DEC_SHIFT_JIS:
        return th_decode_run(dec, out, final, consumed, maxchar, th_dec_shift_jis, 0);
    case TH_DEC_EUC_JP:
        return th_decode_run(dec, out, final, consumed, maxchar, th_dec_euc_jp, 0);
    case TH_DEC_GB18030:
        return th_decode_run(dec, out, final, consumed, maxchar, th_dec_gb18030, 0);
    default: /* TH_DEC_ISO_2022_JP */
        return th_decode_run(dec, out, final, consumed, maxchar, th_dec_iso_2022_jp, 1);
    }
}

/* One code point per byte, and th_decode only calls this once a byte of 0x80 or above is present, which no index maps
   below U+0080. The result therefore always needs at least a Latin-1 str, so build one and widen on the first character
   that will not fit, rather than reading the whole buffer once to find the width and again to fill it. */
static PyObject *th_decode_single_byte(uint8_t single, const unsigned char *bytes, Py_ssize_t len) {
    const uint16_t *table = th_sb_index[single];
    PyObject *narrow = PyUnicode_New(len, 0xFF);
    if (narrow == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_UCS1 *out = PyUnicode_1BYTE_DATA(narrow);
    Py_ssize_t index = 0;
    for (; index < len; index++) {
        uint16_t point = table[bytes[index]];
        if (point > 0xFF) {
            break;
        }
        out[index] = (Py_UCS1)point;
    }
    if (index == len) {
        return narrow;
    }
    Py_DECREF(narrow);
    PyObject *wide = PyUnicode_New(len, 0xFFFF);
    if (wide == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_UCS2 *widened = PyUnicode_2BYTE_DATA(wide);
    for (index = 0; index < len; index++) {
        widened[index] = table[bytes[index]];
    }
    return wide;
}

/* Build the str in its narrowest form, since CPython's equality compares the kind before the content and would report
   a too-wide str as unequal to its own value. One loop per width: PyUnicode_WRITE would branch on the kind for every
   code point. */
static PyObject *th_points_to_str(const Py_UCS4 *points, Py_ssize_t count, Py_UCS4 maxchar) {
    PyObject *decoded = PyUnicode_New(count, maxchar);
    if (decoded == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    if (maxchar > 0xFFFF) {
        memcpy(PyUnicode_4BYTE_DATA(decoded), points, (size_t)count * sizeof(Py_UCS4));
    } else if (maxchar > 0xFF) {
        Py_UCS2 *out = PyUnicode_2BYTE_DATA(decoded);
        for (Py_ssize_t index = 0; index < count; index++) {
            out[index] = (Py_UCS2)points[index];
        }
    } else {
        Py_UCS1 *out = PyUnicode_1BYTE_DATA(decoded);
        for (Py_ssize_t index = 0; index < count; index++) {
            out[index] = (Py_UCS1)points[index];
        }
    }
    return decoded;
}

/* Every legacy decoder emits at most one code point per byte -- a Big5 combination spends two bytes on two code points,
   an error spends one byte on one U+FFFD -- so a len-wide scratch always holds the result. */
static PyObject *th_decode_multi_byte(const th_encoding_entry *entry, const unsigned char *bytes, Py_ssize_t len) {
    Py_UCS4 *scratch = PyMem_Malloc((size_t)len * sizeof(Py_UCS4));
    if (scratch == NULL) {       /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    th_decoder dec;
    th_decode_init(&dec, entry, bytes, len);
    Py_ssize_t consumed = 0;
    Py_UCS4 maxchar = 0;
    Py_ssize_t count = th_decode_chunk(&dec, scratch, 1, &consumed, &maxchar);
    PyObject *decoded = th_points_to_str(scratch, count, maxchar);
    PyMem_Free(scratch);
    return decoded;
}

/* Decode bytes with entry's encoding, per the spec's "decode" entry point: every malformed sequence becomes one U+FFFD
   rather than raising. */
static PyObject *th_decode(const th_encoding_entry *entry, const unsigned char *bytes, Py_ssize_t len) {
    if (entry->kind == TH_DEC_REPLACEMENT) {
        /* the replacement encoding refuses the stateful ISO-2022 and HZ byte streams, which can smuggle markup past a
           sanitizer: a non-empty input decodes to a single U+FFFD */
        return len > 0 ? PyUnicode_FromOrdinal(0xFFFD) : PyUnicode_New(0, 0);
    }
    if (entry->kind == TH_DEC_UTF8) {
        return PyUnicode_Decode((const char *)bytes, len, "utf-8", "replace");
    }
    if (entry->kind == TH_DEC_UTF16LE || entry->kind == TH_DEC_UTF16BE) {
        return PyUnicode_Decode((const char *)bytes, len, entry->kind == TH_DEC_UTF16LE ? "utf-16-le" : "utf-16-be",
                                "replace");
    }
    /* ISO-2022-JP is the one legacy encoding whose escapes can reinterpret an all-ASCII stream */
    if (entry->kind != TH_DEC_ISO_2022_JP && th_decode_ascii_run(bytes, len) == len) {
        return PyUnicode_DecodeASCII((const char *)bytes, len, NULL);
    }
    if (entry->kind == TH_DEC_SINGLE_BYTE) {
        return th_decode_single_byte(entry->single, bytes, len);
    }
    if (len == 0) { /* PyMem_Malloc(0) may return NULL, which the scratch path would read as exhaustion */
        return PyUnicode_New(0, 0);
    }
    return th_decode_multi_byte(entry, bytes, len);
}

#endif /* TURBOHTML_ENCODING_DECODE_H */
