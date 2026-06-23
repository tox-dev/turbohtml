/* Table extraction over a parsed tree, the C engine behind Element.rows()/records() and Node.tables() (wired in
   dom/element.c and dom/node.c).

   A table is read into a dense cell grid: every <tr> belonging to the table (rows inside a nested table are excluded,
   they belong to that table) contributes its <td>/<th> cells left to right, and a rowspan or colspan fills each spanned
   slot with a copy of the cell's text so the result is rectangular and a scraper never has to resolve spans by hand.
   rows() returns the grid as list[list[str]]; records() keys the first row as the header over the rest as list[dict];
   tables() returns rows() for every table in the subtree, nested tables included as their own entries.

   The walk runs entirely in C. Following the free-threading rule, build_grid_locked snapshots the whole grid (cell text
   copied into private C memory) under the per-tree critical section, and the Python list/dict/str objects are built
   from that snapshot afterwards, never while dereferencing live first_child/next_sibling/parent pointers. */

#include "core/common.h"

#include "core/ascii.h"        /* is_space, for trimming cell text */
#include "tokenizer/binding.h" /* Py_BEGIN_CRITICAL_SECTION shim for the GIL/pre-3.13 build */
#include "dom/tree.h"

#include <string.h>

/* Upper bound on a parsed rowspan/colspan. A hostile colspan="99999999" would otherwise size the grid by the attribute
   alone; clamping keeps the allocation bounded while leaving every realistic span exact. */
#define TABLE_SPAN_LIMIT 1000

/* One grid slot: a copy of the cell's trimmed text (NULL for an empty cell), present once a cell or a span reaches it.
 */
typedef struct {
    Py_UCS4 *text;
    Py_ssize_t len;
    int present;
} grid_cell;

/* One grid row, its cells grown on demand as cells and colspans reach further right. */
typedef struct {
    grid_cell *cells;
    Py_ssize_t cap;
} grid_row;

/* A table's cell grid: row_count rows, each independently as wide as it needs to be. */
typedef struct {
    grid_row *rows;
    Py_ssize_t row_count;
} table_grid;

static void free_grid(table_grid *grid) {
    for (Py_ssize_t row = 0; row < grid->row_count; row++) {
        grid_row *current = &grid->rows[row];
        for (Py_ssize_t col = 0; col < current->cap; col++) {
            PyMem_Free(current->cells[col].text);
        }
        PyMem_Free(current->cells);
    }
    PyMem_Free(grid->rows);
    grid->rows = NULL;
    grid->row_count = 0;
}

/* The cell's span for name_atom (rowspan/colspan): the parsed non-negative integer (clamped to TABLE_SPAN_LIMIT), or -1
   when the attribute is absent, valueless, or has no leading digits, so the caller can apply the per-span default. */
static Py_ssize_t parse_span(th_node *cell, uint32_t name_atom) {
    const th_node_attr *attr = NULL;
    for (Py_ssize_t index = 0; index < cell->attr_count; index++) {
        if (cell->attrs[index].name_atom == name_atom) {
            attr = &cell->attrs[index];
            break;
        }
    }
    if (attr == NULL || attr->value == NULL) {
        return -1;
    }
    const Py_UCS4 *value = attr->value;
    Py_ssize_t length = attr->value_len;
    Py_ssize_t index = 0;
    while (index < length && is_space(value[index])) {
        index++;
    }
    Py_ssize_t number = 0;
    int digits = 0;
    while (index < length && value[index] >= '0' && value[index] <= '9') {
        number = number * 10 + (value[index] - '0');
        if (number > TABLE_SPAN_LIMIT) {
            number = TABLE_SPAN_LIMIT;
        }
        index++;
        digits = 1;
    }
    return digits ? number : -1;
}

/* Grow row so column index `needed - 1` is addressable, zero-initializing the new slots. -1 on allocation failure. */
static int ensure_columns(grid_row *row, Py_ssize_t needed) {
    if (needed <= row->cap) {
        return 0;
    }
    grid_cell *cells = PyMem_Realloc(row->cells, (size_t)needed * sizeof(grid_cell));
    if (cells == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (Py_ssize_t index = row->cap; index < needed; index++) {
        cells[index].text = NULL;
        cells[index].len = 0;
        cells[index].present = 0;
    }
    row->cells = cells;
    row->cap = needed;
    return 0;
}

/* Place a row's <td>/<th> cells into the grid, honoring spans: each cell takes the next free column, and a rowspan or
   colspan fills every covered slot with a fresh copy of the trimmed cell text. -1 on allocation failure. */
static int fill_row(th_tree *tree, table_grid *grid, Py_ssize_t row_index, th_node *tr) {
    Py_ssize_t column = 0;
    for (th_node *child = tr->first_child; child != NULL; child = child->next_sibling) {
        if (child->type != TH_NODE_ELEMENT || (child->atom != TH_TAG_TD && child->atom != TH_TAG_TH)) {
            continue;
        }
        grid_row *home = &grid->rows[row_index];
        while (column < home->cap && home->cells[column].present) {
            column++;
        }
        Py_ssize_t colspan = parse_span(child, TH_ATTR_COLSPAN);
        colspan = colspan <= 0 ? 1 : colspan; /* absent (-1) or a 0 span both mean one column */
        Py_ssize_t remaining = grid->row_count - row_index;
        Py_ssize_t rowspan = parse_span(child, TH_ATTR_ROWSPAN);
        if (rowspan < 0) {
            rowspan = 1; /* absent: a single row */
        } else if (rowspan == 0 || rowspan > remaining) {
            rowspan = remaining; /* 0 spans to the end of the table; an overlong span clamps to the rows that exist */
        }
        Py_ssize_t text_len;
        Py_UCS4 *raw = th_node_text(tree, child, &text_len);
        if (raw == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            return -1;     /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        Py_ssize_t start = 0;
        Py_ssize_t end = text_len;
        while (start < end && is_space(raw[start])) {
            start++;
        }
        while (end > start && is_space(raw[end - 1])) {
            end--;
        }
        for (Py_ssize_t row_offset = 0; row_offset < rowspan; row_offset++) {
            grid_row *target = &grid->rows[row_index + row_offset];
            if (ensure_columns(target, column + colspan) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
                PyMem_Free(raw);                                /* GCOVR_EXCL_LINE: allocation-failure path */
                return -1;                                      /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            for (Py_ssize_t col_offset = 0; col_offset < colspan; col_offset++) {
                grid_cell *slot = &target->cells[column + col_offset];
                if (slot->present) { /* an overlapping span already wrote here; the later cell wins */
                    PyMem_Free(slot->text);
                }
                slot->present = 1;
                slot->len = end - start;
                slot->text = NULL;
                if (end > start) {
                    slot->text = PyMem_Malloc((size_t)(end - start) * sizeof(Py_UCS4));
                    if (slot->text == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced */
                        slot->present = 0;    /* GCOVR_EXCL_LINE: allocation-failure path */
                        slot->len = 0;        /* GCOVR_EXCL_LINE: allocation-failure path */
                        PyMem_Free(raw);      /* GCOVR_EXCL_LINE: allocation-failure path */
                        return -1;            /* GCOVR_EXCL_LINE: allocation-failure path */
                    }
                    memcpy(slot->text, &raw[start], (size_t)(end - start) * sizeof(Py_UCS4));
                }
            }
        }
        PyMem_Free(raw);
        column += colspan;
    }
    return 0;
}

/* Collect the <tr> elements that belong to `table` in document order, skipping any nested table's subtree so its rows
   are not stolen. *rows is a PyMem array grown as needed. -1 on allocation failure. */
static int collect_table_rows(th_node *table, th_node ***rows, Py_ssize_t *count, Py_ssize_t *cap) {
    th_node *current = table->first_child;
    while (current != NULL) {
        int nested_table = current->type == TH_NODE_ELEMENT && current->atom == TH_TAG_TABLE;
        if (current->type == TH_NODE_ELEMENT && current->atom == TH_TAG_TR) {
            if (*count == *cap) {
                Py_ssize_t grown = *cap == 0 ? 8 : *cap * 2;
                th_node **resized = PyMem_Realloc(*rows, (size_t)grown * sizeof(th_node *));
                if (resized == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                    return -1;         /* GCOVR_EXCL_LINE: allocation-failure path */
                }
                *rows = resized;
                *cap = grown;
            }
            (*rows)[(*count)++] = current;
        }
        if (!nested_table && current->first_child != NULL) {
            current = current->first_child;
            continue;
        }
        while (current != table && current->next_sibling == NULL) {
            current = current->parent;
        }
        current = current == table ? NULL : current->next_sibling;
    }
    return 0;
}

/* Snapshot `table` into `grid` under the caller's critical section: gather its rows, then fill the dense grid. -1 on
   allocation failure (the partially built grid is left for free_grid to release). */
static int build_grid_locked(th_tree *tree, th_node *table, table_grid *grid) {
    th_node **rows = NULL;
    Py_ssize_t count = 0;
    Py_ssize_t cap = 0;
    grid->rows = NULL;
    grid->row_count = 0;
    if (collect_table_rows(table, &rows, &count, &cap) < 0) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        PyMem_Free(rows);                                     /* GCOVR_EXCL_LINE: allocation-failure path */
        return -1;                                            /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    grid->row_count = count;
    if (count > 0) {
        grid->rows = PyMem_Calloc((size_t)count, sizeof(grid_row));
        if (grid->rows == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            grid->row_count = 0;  /* GCOVR_EXCL_LINE: allocation-failure path */
            PyMem_Free(rows);     /* GCOVR_EXCL_LINE: allocation-failure path */
            return -1;            /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    for (Py_ssize_t index = 0; index < count; index++) {
        if (fill_row(tree, grid, index, rows[index]) < 0) { /* GCOVR_EXCL_BR_LINE: fill only fails on OOM */
            PyMem_Free(rows);                               /* GCOVR_EXCL_LINE: allocation-failure path */
            return -1;                                      /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    PyMem_Free(rows);
    return 0;
}

/* The number of columns the widest row reaches; the rectangular width every row is padded to. A row's rightmost slot is
   always filled (cells are placed and spans grow the row left to right), so its width is simply its allocated cap. */
static Py_ssize_t grid_width(const table_grid *grid) {
    Py_ssize_t width = 0;
    for (Py_ssize_t row = 0; row < grid->row_count; row++) {
        if (grid->rows[row].cap > width) {
            width = grid->rows[row].cap;
        }
    }
    return width;
}

/* The text at (row, col) as a str: the trimmed cell text, or "" for an empty or never-filled slot. */
static PyObject *cell_to_str(const grid_row *row, Py_ssize_t col) {
    if (col < row->cap && row->cells[col].present && row->cells[col].text != NULL) {
        return PyUnicode_FromKindAndData(PyUnicode_4BYTE_KIND, row->cells[col].text, row->cells[col].len);
    }
    return PyUnicode_FromString("");
}

/* Build list[list[str]] from the snapshot: every row padded to the table width. */
static PyObject *grid_to_rows(const table_grid *grid) {
    Py_ssize_t width = grid_width(grid);
    PyObject *result = PyList_New(grid->row_count);
    if (result == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    for (Py_ssize_t row = 0; row < grid->row_count; row++) {
        PyObject *cells = PyList_New(width);
        if (cells == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            Py_DECREF(result); /* GCOVR_EXCL_LINE: allocation-failure path */
            return NULL;       /* GCOVR_EXCL_LINE: allocation-failure path */
        }
        for (Py_ssize_t col = 0; col < width; col++) {
            PyObject *value = cell_to_str(&grid->rows[row], col);
            if (value == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                Py_DECREF(cells);  /* GCOVR_EXCL_LINE: allocation-failure path */
                Py_DECREF(result); /* GCOVR_EXCL_LINE: allocation-failure path */
                return NULL;       /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            PyList_SET_ITEM(cells, col, value);
        }
        PyList_SET_ITEM(result, row, cells);
    }
    return result;
}

/* Build list[dict[str, str]] from the snapshot: row 0 is the header, each later row a dict keyed by it. An empty grid
   yields []; a header-only grid yields []. A duplicated header keeps the rightmost column's value (dict semantics). */
static PyObject *grid_to_records(const table_grid *grid) {
    if (grid->row_count == 0) {
        return PyList_New(0);
    }
    Py_ssize_t width = grid_width(grid);
    PyObject *result = PyList_New(0);
    if (result == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;      /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    PyObject **headers = PyMem_Calloc((size_t)(width > 0 ? width : 1), sizeof(PyObject *));
    if (headers == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        Py_DECREF(result); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    int failed = 0;
    for (Py_ssize_t col = 0; col < width; col++) {
        headers[col] = cell_to_str(&grid->rows[0], col);
        if (headers[col] == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            failed = 1;             /* GCOVR_EXCL_LINE: allocation-failure path */
            break;                  /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    if (!failed) { /* GCOVR_EXCL_BR_LINE: failed is set only on an allocation failure building the header */
        for (Py_ssize_t row = 1; row < grid->row_count; row++) {
            PyObject *record = PyDict_New();
            if (record == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                failed = 1;       /* GCOVR_EXCL_LINE: allocation-failure path */
                break;            /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            for (Py_ssize_t col = 0; col < width; col++) {
                PyObject *value = cell_to_str(&grid->rows[row], col);
                if (value == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                    failed = 1;      /* GCOVR_EXCL_LINE: allocation-failure path */
                    break;           /* GCOVR_EXCL_LINE: allocation-failure path */
                }
                int rc = PyDict_SetItem(record, headers[col], value);
                Py_DECREF(value);
                if (rc < 0) {   /* GCOVR_EXCL_BR_LINE: a dict set only fails on allocation failure */
                    failed = 1; /* GCOVR_EXCL_LINE: allocation-failure path */
                    break;      /* GCOVR_EXCL_LINE: allocation-failure path */
                }
            }
            if (!failed) { /* GCOVR_EXCL_BR_LINE: failed is set only on an allocation failure populating the record */
                failed = PyList_Append(result, record) < 0; /* GCOVR_EXCL_BR_LINE: append only fails on allocation */
            }
            Py_DECREF(record);
            if (failed) { /* GCOVR_EXCL_BR_LINE: failed is set only on an allocation failure */
                break;    /* GCOVR_EXCL_LINE: allocation-failure path */
            }
        }
    }
    for (Py_ssize_t col = 0; col < width; col++) {
        Py_XDECREF(headers[col]);
    }
    PyMem_Free(headers);
    if (failed) {          /* GCOVR_EXCL_BR_LINE: failed is set only on an allocation failure */
        Py_DECREF(result); /* GCOVR_EXCL_LINE: allocation-failure path */
        return NULL;       /* GCOVR_EXCL_LINE: allocation-failure path */
    }
    return result;
}

/* The next node in a full pre-order walk of root's subtree (descending into every element, tables included so nested
   ones are found), or NULL at the end. */
static th_node *next_in_subtree(th_node *current, th_node *root) {
    if (current->first_child != NULL) {
        return current->first_child;
    }
    while (current != root && current->next_sibling == NULL) {
        current = current->parent;
    }
    return current == root ? NULL : current->next_sibling;
}

/* Element.rows() -> list[list[str]]. Snapshot the table under the per-tree lock, then materialize the grid. */
PyObject *turbohtml_element_table_rows(PyObject *owner, th_tree *tree, th_node *table) {
    table_grid grid = {NULL, 0};
    int failed;
    PyObject *handle = turbohtml_node_handle(owner);
    (void)handle;                      /* only the per-tree lock on free-threaded builds; a no-op argument otherwise */
    Py_BEGIN_CRITICAL_SECTION(handle); /* per-tree lock: no concurrent mutation mid-walk */
    failed = build_grid_locked(tree, table, &grid) < 0;
    Py_END_CRITICAL_SECTION();
    PyObject *result = failed ? NULL : grid_to_rows(&grid); /* GCOVR_EXCL_BR_LINE: build only fails on OOM */
    free_grid(&grid);
    return result;
}

/* Element.records() -> list[dict[str, str]]. Same snapshot, keyed by the first row. */
PyObject *turbohtml_element_table_records(PyObject *owner, th_tree *tree, th_node *table) {
    table_grid grid = {NULL, 0};
    int failed;
    PyObject *handle = turbohtml_node_handle(owner);
    (void)handle;                      /* only the per-tree lock on free-threaded builds; a no-op argument otherwise */
    Py_BEGIN_CRITICAL_SECTION(handle); /* per-tree lock: no concurrent mutation mid-walk */
    failed = build_grid_locked(tree, table, &grid) < 0;
    Py_END_CRITICAL_SECTION();
    PyObject *result = failed ? NULL : grid_to_records(&grid); /* GCOVR_EXCL_BR_LINE: build only fails on OOM */
    free_grid(&grid);
    return result;
}

/* Node.tables() -> list[list[list[str]]]. Snapshot every table in the subtree (nested tables included as their own
   entries) under one critical section, then materialize each as rows(). */
PyObject *turbohtml_node_tables(PyObject *owner, th_tree *tree, th_node *root) {
    table_grid *grids = NULL;
    Py_ssize_t grid_count = 0;
    Py_ssize_t grid_cap = 0;
    int failed = 0;
    PyObject *handle = turbohtml_node_handle(owner);
    (void)handle;                      /* only the per-tree lock on free-threaded builds; a no-op argument otherwise */
    Py_BEGIN_CRITICAL_SECTION(handle); /* per-tree lock: no concurrent mutation mid-walk */
    for (th_node *current = root->first_child; current != NULL; current = next_in_subtree(current, root)) {
        if (current->type != TH_NODE_ELEMENT || current->atom != TH_TAG_TABLE) {
            continue;
        }
        if (grid_count == grid_cap) {
            Py_ssize_t grown = grid_cap == 0 ? 4 : grid_cap * 2;
            table_grid *resized = PyMem_Realloc(grids, (size_t)grown * sizeof(table_grid));
            if (resized == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                failed = 1;        /* GCOVR_EXCL_LINE: allocation-failure path */
                break;             /* GCOVR_EXCL_LINE: allocation-failure path */
            }
            grids = resized;
            grid_cap = grown;
        }
        failed = build_grid_locked(tree, current, &grids[grid_count]) < 0;
        grid_count++;
        if (failed) { /* GCOVR_EXCL_BR_LINE: build only fails on OOM; the partial grid is counted so it is freed */
            break;    /* GCOVR_EXCL_LINE: allocation-failure path */
        }
    }
    Py_END_CRITICAL_SECTION();
    PyObject *result = NULL;
    if (!failed) { /* GCOVR_EXCL_BR_LINE: failed is set only on an allocation failure */
        result = PyList_New(grid_count);
        if (result != NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
            for (Py_ssize_t index = 0; index < grid_count; index++) {
                PyObject *rows = grid_to_rows(&grids[index]);
                if (rows == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
                    Py_CLEAR(result); /* GCOVR_EXCL_LINE: allocation-failure path */
                    break;            /* GCOVR_EXCL_LINE: allocation-failure path */
                }
                PyList_SET_ITEM(result, index, rows);
            }
        }
    }
    for (Py_ssize_t index = 0; index < grid_count; index++) {
        free_grid(&grids[index]);
    }
    PyMem_Free(grids);
    return result;
}
