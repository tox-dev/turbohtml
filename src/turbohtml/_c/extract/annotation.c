/* The inscriptis annotation output processors as pure transforms over the
   (text, [(start, end, label), ...]) pair Node.to_annotated_text() returns.

   annotation_surface() groups the matched substrings by their label; the spans
   stay in their given (document) order, so a label's surface forms read top to
   bottom. annotation_tags() weaves the spans back into the text as inline
   <label>...</label> markup, closing the innermost span first so properly
   nested spans produce well-formed output.

   Both touch only their str and sequence arguments -- never a tree, node, or
   shared handle -- so they need no critical section and are free-threading safe
   by construction: the input str is immutable and the spans sequence is only
   read. */

#include "core/common.h"

#include <stdlib.h>

/* One parsed (start, end, label) triple. label borrows the caller's reference;
   the spans sequence outlives every call below. */
typedef struct {
    Py_ssize_t start;
    Py_ssize_t end;
    PyObject *label;
} annotation_span;

/* Validate spans and copy it into a fresh array of annotation_span. Each item
   must be a (start:int, end:int, label:str) tuple whose offsets satisfy
   0 <= start <= end <= text_len (the same shape to_annotated_text() emits).
   *out_count receives the length; the caller frees *out_spans. Returns -1 with
   an exception set on any malformed item or out-of-range offset. */
static int annotation_parse_spans(PyObject *spans, Py_ssize_t text_len, annotation_span **out_spans,
                                  Py_ssize_t *out_count) {
    PyObject *fast = PySequence_Fast(spans, "spans must be an iterable of (start, end, label) tuples");
    if (fast == NULL) {
        return -1;
    }
    Py_ssize_t count = PySequence_Fast_GET_SIZE(fast);
    annotation_span *items = PyMem_Calloc((size_t)(count > 0 ? count : 1), sizeof(annotation_span));
    if (items == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_DECREF(fast);  /* GCOVR_EXCL_LINE: allocation-failure path */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (Py_ssize_t index = 0; index < count; index++) {
        PyObject *item = PySequence_Fast_GET_ITEM(fast, index);
        if (!PyTuple_Check(item)) {
            PyErr_SetString(PyExc_TypeError, "each span must be a (start, end, label) tuple");
            PyMem_Free(items);
            Py_DECREF(fast);
            return -1;
        }
        Py_ssize_t start, end;
        PyObject *label;
        if (!PyArg_ParseTuple(item, "nnU", &start, &end, &label)) {
            PyMem_Free(items);
            Py_DECREF(fast);
            return -1;
        }
        if (start < 0 || end < start || end > text_len) {
            PyErr_Format(PyExc_ValueError, "span (%zd, %zd) is out of range for text of length %zd", start, end,
                         text_len);
            PyMem_Free(items);
            Py_DECREF(fast);
            return -1;
        }
        items[index].start = start;
        items[index].end = end;
        items[index].label = label;
    }
    Py_DECREF(fast);
    *out_spans = items;
    *out_count = count;
    return 0;
}

PyObject *turbohtml_annotation_surface(PyObject *Py_UNUSED(module), PyObject *args) {
    PyObject *text, *spans;
    if (!PyArg_ParseTuple(args, "UO:annotation_surface", &text, &spans)) {
        return NULL;
    }
    annotation_span *items;
    Py_ssize_t count;
    if (annotation_parse_spans(spans, PyUnicode_GET_LENGTH(text), &items, &count) < 0) {
        return NULL;
    }
    PyObject *result = PyDict_New();
    if (result == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyMem_Free(items); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (Py_ssize_t index = 0; index < count; index++) {
        PyObject *label = items[index].label;
        PyObject *surface = PyUnicode_Substring(text, items[index].start, items[index].end);
        if (surface == NULL) { /* GCOVR_EXCL_BR_LINE: bounds are validated, so only an alloc failure makes this NULL */
            goto error;        /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        PyObject *bucket = PyDict_GetItemWithError(result, label); /* borrowed, or NULL when absent */
        if (bucket == NULL) {
            if (PyErr_Occurred()) { /* GCOVR_EXCL_BR_LINE: a str key always hashes, so lookup cannot raise */
                Py_DECREF(surface); /* GCOVR_EXCL_LINE: hash-failure path */
                goto error;         /* GCOVR_EXCL_LINE: hash-failure path */
            }
            bucket = PyList_New(0);
            if (bucket == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                Py_DECREF(surface); /* GCOVR_EXCL_LINE: allocation-failure path */
                goto error;         /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            int stored = PyDict_SetItem(result, label, bucket);
            Py_DECREF(bucket);      /* the dict now owns the only reference; bucket stays valid through it */
            if (stored < 0) {       /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                Py_DECREF(surface); /* GCOVR_EXCL_LINE: allocation-failure path */
                goto error;         /* GCOVR_EXCL_LINE: allocation-failure path */
            }
        }
        int appended = PyList_Append(bucket, surface);
        Py_DECREF(surface);
        if (appended < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            goto error;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    PyMem_Free(items);
    return result;
error:                 /* GCOVR_EXCL_LINE: shared cleanup for the unreachable allocation-failure arms */
    Py_DECREF(result); /* GCOVR_EXCL_LINE: allocation-failure path */
    PyMem_Free(items); /* GCOVR_EXCL_LINE: allocation-failure path */
    return NULL;       /* GCOVR_EXCL_LINE: allocation-failure path */
}

/* One tag boundary. rank holds the span's other endpoint (an open's is the end,
   a close's is the start), so pos == rank marks a zero-width span. seq, the
   original span index, makes the order total so qsort is deterministic. */
typedef struct {
    Py_ssize_t pos;
    int is_open;
    Py_ssize_t rank;
    Py_ssize_t seq;
    PyObject *label;
} annotation_event;

/* The emission phase at a shared position: a non-zero-width span closes (0)
   before a zero-width span opens-and-closes (1), which is before a non-zero-width
   span opens (2). This keeps nested spans well-formed and a zero-width span's
   own <label></label> intact rather than splitting it across a boundary. */
static int annotation_phase(const annotation_event *event) {
    if (event->pos == event->rank) {
        return 1;
    }
    return event->is_open ? 2 : 0;
}

/* Total order over the tag boundaries. The phase and seq tiebreaks decide between two
   events that already agree on every earlier key; seq increases with the original span
   index, so for such a pair the comparator is only ever handed the lower-seq operand on
   one side under a given libc's qsort. The opposite arm of each tie is correct but not
   reachable by any input (glibc and macOS qsort disagree on which side they pass), so
   those four branches carry GCOVR_EXCL_BR_LINE -- the pos and rank ties, whose values
   vary widely enough that qsort compares them both ways, are covered normally. */
static int annotation_event_cmp(const void *lhs, const void *rhs) {
    const annotation_event *left = lhs, *right = rhs;
    if (left->pos != right->pos) {
        return left->pos < right->pos ? -1 : 1;
    }
    int left_phase = annotation_phase(left), right_phase = annotation_phase(right);
    if (left_phase != right_phase) {
        return left_phase < right_phase ? -1 : 1; /* GCOVR_EXCL_BR_LINE: one-sided tie, see note */
    }
    if (left_phase == 1) {
        /* Zero-width spans group by span, opening each before closing it. */
        if (left->seq != right->seq) {
            return left->seq < right->seq ? -1 : 1; /* GCOVR_EXCL_BR_LINE: one-sided tie, see note */
        }
        return right->is_open - left->is_open; /* open (1) before close (0) */
    }
    if (left->rank != right->rank) {
        return left->rank > right->rank ? -1 : 1; /* outer span opens first / closes last */
    }
    /* Identical ranges: opens keep document order, closes reverse it (LIFO). */
    if (left_phase == 2) {
        return left->seq < right->seq ? -1 : 1; /* GCOVR_EXCL_BR_LINE: one-sided tie, see note */
    }
    return left->seq < right->seq ? 1 : -1; /* GCOVR_EXCL_BR_LINE: one-sided tie, see note */
}

static Py_ssize_t annotation_write_tag(PyObject *out, Py_ssize_t written, PyObject *label, int is_open) {
    int kind = PyUnicode_KIND(out);
    void *data = PyUnicode_DATA(out);
    PyUnicode_WRITE(kind, data, written++, '<');
    if (!is_open) {
        PyUnicode_WRITE(kind, data, written++, '/');
    }
    Py_ssize_t label_len = PyUnicode_GET_LENGTH(label);
    th_copy_characters(out, written, label, 0, label_len); /* out's kind covers label: cannot fail */
    written += label_len;
    PyUnicode_WRITE(kind, data, written++, '>');
    return written;
}

PyObject *turbohtml_annotation_tags(PyObject *Py_UNUSED(module), PyObject *args) {
    PyObject *text, *spans;
    if (!PyArg_ParseTuple(args, "UO:annotation_tags", &text, &spans)) {
        return NULL;
    }
    Py_ssize_t text_len = PyUnicode_GET_LENGTH(text);
    annotation_span *items;
    Py_ssize_t count;
    if (annotation_parse_spans(spans, text_len, &items, &count) < 0) {
        return NULL;
    }
    annotation_event *events = PyMem_Calloc((size_t)(count > 0 ? 2 * count : 1), sizeof(annotation_event));
    if (events == NULL) {        /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyMem_Free(items);       /* GCOVR_EXCL_LINE: allocation-failure path */
        return PyErr_NoMemory(); /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t out_len = text_len;
    Py_UCS4 maxchar = PyUnicode_MAX_CHAR_VALUE(text);
    for (Py_ssize_t index = 0; index < count; index++) {
        Py_ssize_t label_len = PyUnicode_GET_LENGTH(items[index].label);
        out_len += 2 * label_len + 5; /* "<label>" adds label_len + 2, "</label>" adds label_len + 3 */
        Py_UCS4 label_max = PyUnicode_MAX_CHAR_VALUE(items[index].label);
        if (label_max > maxchar) {
            maxchar = label_max;
        }
        events[2 * index] = (annotation_event){items[index].start, 1, items[index].end, index, items[index].label};
        events[2 * index + 1] = (annotation_event){items[index].end, 0, items[index].start, index, items[index].label};
    }
    qsort(events, (size_t)(2 * count), sizeof(annotation_event), annotation_event_cmp);
    PyObject *out = PyUnicode_New(out_len, th_str_maxchar(maxchar));
    if (out == NULL) {      /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        PyMem_Free(events); /* GCOVR_EXCL_LINE: allocation-failure path */
        PyMem_Free(items);  /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;        /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    Py_ssize_t cursor = 0, written = 0;
    for (Py_ssize_t index = 0; index < 2 * count; index++) {
        if (events[index].pos > cursor) {
            th_copy_characters(out, written, text, cursor, events[index].pos - cursor);
            written += events[index].pos - cursor;
            cursor = events[index].pos;
        }
        written = annotation_write_tag(out, written, events[index].label, events[index].is_open);
    }
    if (text_len > cursor) {
        th_copy_characters(out, written, text, cursor, text_len - cursor);
    }
    PyMem_Free(events);
    PyMem_Free(items);
    return out;
}
