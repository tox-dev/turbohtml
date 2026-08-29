/* _PhoneConfig: the validated, table-resolved form of a PhoneNumbers value.

   Linker and LinkDetector compile their PhoneNumbers once through _phone_config_compile and hand the object to every
   scan, so a call passes one pointer: the region indexes, the flags, the national floor the regions imply, the label
   table in memory the object owns, and the two Python classes a detected number is built from. The object is not
   constructible from Python and carries no module-global state; PhoneNumber and PhoneType travel inside it. */

#include "clean/phone_binding.h"

#include <string.h>

#define SPEC_ITEMS 11
#define MAX_LABELS 256
#define MAX_LABEL_LENGTH 12
#define TYPE_MEMBERS 12
#define ALL_TYPES 0x7FFu
#define MAX_NSN (TH_PHONE_NSN_CAPACITY - 1)

static void free_config_tables(PhoneConfigObject *self) {
    PyMem_Free(self->labels);
    PyMem_Free(self->label_bytes);
    self->labels = NULL;
    self->label_bytes = NULL;
}

static int phone_config_traverse(PyObject *self, visitproc visit, void *arg) {
    PhoneConfigObject *config = (PhoneConfigObject *)self;
    Py_VISIT(Py_TYPE(self));       /* GCOVR_EXCL_BR_LINE: the type is non-NULL for the object's lifetime */
    Py_VISIT(config->number_type); /* GCOVR_EXCL_BR_LINE: set before the object is tracked, dropped only in dealloc */
    Py_VISIT(config->types);       /* GCOVR_EXCL_BR_LINE: same */
    return 0;
}

static int phone_config_clear(PyObject *self) {
    PhoneConfigObject *config = (PhoneConfigObject *)self;
    Py_CLEAR(config->number_type);
    Py_CLEAR(config->types);
    return 0;
}

static void phone_config_dealloc(PyObject *self) {
    PyTypeObject *type = Py_TYPE(self);
    /* a failed compile does not track the object; untracking an untracked object is a no-op */
    PyObject_GC_UnTrack(self);
    free_config_tables((PhoneConfigObject *)self);
    (void)phone_config_clear(self);
    type->tp_free(self);
    Py_DECREF(type);
}

PyDoc_STRVAR(phone_config_doc, "A compiled PhoneNumbers configuration; built by _phone_config_compile.");

/* The compiled tables do not change, so a copy is the object itself; dataclasses.asdict deep-copies each field of
   the settings that hold one. */
static PyObject *phone_config_copy(PyObject *self, PyObject *Py_UNUSED(args)) {
    return Py_NewRef(self);
}

static PyMethodDef phone_config_methods[] = {
    {"__copy__", phone_config_copy, METH_NOARGS, "Return the configuration itself; it is immutable."},
    {"__deepcopy__", phone_config_copy, METH_O, "Return the configuration itself; it is immutable."},
    {NULL, NULL, 0, NULL},
};

static PyType_Slot phone_config_slots[] = {
    {Py_tp_doc, (void *)phone_config_doc}, {Py_tp_methods, phone_config_methods},
    {Py_tp_dealloc, phone_config_dealloc}, {Py_tp_traverse, phone_config_traverse},
    {Py_tp_clear, phone_config_clear},     TH_SEALED_END,
};

static PyType_Spec phone_config_spec = {
    .name = "turbohtml._html._PhoneConfig",
    .basicsize = sizeof(PhoneConfigObject),
    .flags = Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | TH_SEALED,
    .slots = phone_config_slots,
};

int turbohtml_phone_config_check(PyObject *module, PyObject *object) {
    module_state *state = PyModule_GetState(module);
    return Py_IS_TYPE(object, (PyTypeObject *)state->phone_config_type);
}

static int parse_regions(PyObject *regions, th_phone_config *config) {
    if (!PyTuple_Check(regions)) {
        PyErr_SetString(PyExc_TypeError, "phone regions must be a tuple");
        return -1;
    }
    Py_ssize_t count = PyTuple_GET_SIZE(regions);
    if (count > TH_PHONE_MAX_REGIONS) {
        PyErr_Format(PyExc_ValueError, "at most %d phone regions, got %zd", TH_PHONE_MAX_REGIONS, count);
        return -1;
    }
    for (Py_ssize_t index = 0; index < count; index++) {
        PyObject *item = PyTuple_GET_ITEM(regions, index);
        if (!PyUnicode_Check(item)) {
            PyErr_SetString(PyExc_TypeError, "phone regions must be str");
            return -1;
        }
        Py_ssize_t length;
        const char *code = PyUnicode_AsUTF8AndSize(item, &length);
        if (code == NULL) {
            return -1;
        }
        int region = length == 2 && code[0] >= 'A' && code[0] <= 'Z' && code[1] >= 'A' && code[1] <= 'Z'
                         ? th_phone_region_index(code, 2)
                         : -1;
        if (region < 0) {
            PyErr_Format(PyExc_ValueError, "unknown phone region %R", item);
            return -1;
        }
        for (Py_ssize_t earlier = 0; earlier < index; earlier++) {
            if (config->regions[earlier] == region) {
                PyErr_Format(PyExc_ValueError, "duplicate phone region %R", item);
                return -1;
            }
        }
        config->regions[index] = (uint16_t)region;
    }
    config->region_count = (uint8_t)count;
    return 0;
}

static int parse_flag(PyObject *value, const char *name, uint8_t *out) {
    if (!PyBool_Check(value)) {
        PyErr_Format(PyExc_TypeError, "%s must be bool", name);
        return -1;
    }
    *out = value == Py_True;
    return 0;
}

static int parse_type_mask(PyObject *value, const th_phone_config *config, uint16_t *out) {
    if (!PyLong_CheckExact(value)) {
        PyErr_SetString(PyExc_TypeError, "phone type mask must be int");
        return -1;
    }
    long mask = PyLong_AsLong(value);
    if (mask < 1 || mask > (long)ALL_TYPES) {
        PyErr_SetString(PyExc_ValueError, "phone type mask must be within 1..0x7FF");
        return -1;
    }
    if (!config->require_valid && mask != (long)ALL_TYPES) {
        PyErr_SetString(PyExc_ValueError, "phone types can only be restricted with require_valid");
        return -1;
    }
    *out = (uint16_t)mask;
    return 0;
}

static int parse_grouping(PyObject *value, const th_phone_config *config, uint8_t *out) {
    if (!PyLong_CheckExact(value)) {
        PyErr_SetString(PyExc_TypeError, "grouping must be int");
        return -1;
    }
    long grouping = PyLong_AsLong(value);
    if (grouping < TH_PHONE_GROUPING_ANY || grouping > TH_PHONE_GROUPING_EXACT) {
        PyErr_SetString(PyExc_ValueError, "grouping must be between 0 and 2");
        return -1;
    }
    if (grouping != TH_PHONE_GROUPING_ANY && !config->require_valid) {
        PyErr_SetString(PyExc_ValueError, "phone grouping can only be checked with require_valid");
        return -1;
    }
    *out = (uint8_t)grouping;
    return 0;
}

static int label_is_well_formed(const char *text, Py_ssize_t length) {
    if (length < 1 || length > MAX_LABEL_LENGTH) {
        return 0;
    }
    static const char allowed[] = "abcdefghijklmnopqrstuvwxyz";
    for (Py_ssize_t offset = 0; offset < length; offset++) {
        if (memchr(allowed, text[offset], sizeof(allowed) - 1) == NULL) {
            return 0;
        }
    }
    return 1;
}

/* Copy the labels into memory the object owns, checking each is short lowercase ASCII and the tuple is strictly
   ascending, the order th_phone_find's binary search relies on. */
static int parse_labels(PyObject *labels, PhoneConfigObject *self) {
    if (!PyTuple_Check(labels)) {
        PyErr_SetString(PyExc_TypeError, "phone labels must be a tuple");
        return -1;
    }
    Py_ssize_t count = PyTuple_GET_SIZE(labels);
    if (count > MAX_LABELS) {
        PyErr_Format(PyExc_ValueError, "at most %d phone labels, got %zd", MAX_LABELS, count);
        return -1;
    }
    self->labels = PyMem_Malloc(sizeof(th_phone_label) * (size_t)(count ? count : 1));
    self->label_bytes = PyMem_Malloc((size_t)count * MAX_LABEL_LENGTH + 1);
    if (self->labels == NULL || self->label_bytes == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure */
        PyErr_NoMemory();                                    /* GCOVR_EXCL_LINE */
        return -1;                                           /* GCOVR_EXCL_LINE */
    }
    char *cursor = self->label_bytes;
    for (Py_ssize_t index = 0; index < count; index++) {
        PyObject *item = PyTuple_GET_ITEM(labels, index);
        if (!PyUnicode_Check(item)) {
            PyErr_SetString(PyExc_TypeError, "phone labels must be str");
            return -1;
        }
        Py_ssize_t length;
        const char *text = PyUnicode_AsUTF8AndSize(item, &length);
        if (text == NULL) {
            return -1;
        }
        if (!label_is_well_formed(text, length)) {
            PyErr_Format(PyExc_ValueError, "phone label %R must be 1-12 lowercase ASCII letters", item);
            return -1;
        }
        if (index > 0) {
            const th_phone_label *previous = &self->labels[index - 1];
            size_t shared = previous->len < (size_t)length ? previous->len : (size_t)length;
            int order = memcmp(previous->text, text, shared);
            if (order > 0 || (order == 0 && previous->len >= (size_t)length)) {
                PyErr_Format(PyExc_ValueError, "phone labels must be sorted and distinct, %R is out of order", item);
                return -1;
            }
        }
        memcpy(cursor, text, (size_t)length);
        self->labels[index].text = cursor;
        self->labels[index].len = (uint8_t)length;
        cursor += length;
    }
    self->config.labels = self->labels;
    self->config.label_count = (size_t)count;
    return 0;
}

static int parse_classes(PyObject *number_type, PyObject *types, PhoneConfigObject *self) {
    if (!PyType_Check(number_type)) {
        PyErr_SetString(PyExc_TypeError, "phone_number_type must be a class");
        return -1;
    }
    PyObject *factory = PyObject_GetAttrString(number_type, "_from_native");
    if (factory == NULL) {
        return -1;
    }
    int callable = PyCallable_Check(factory);
    Py_DECREF(factory);
    if (!callable) {
        PyErr_SetString(PyExc_TypeError, "phone_number_type._from_native must be callable");
        return -1;
    }
    if (!PyTuple_Check(types) || PyTuple_GET_SIZE(types) != TYPE_MEMBERS) {
        PyErr_Format(PyExc_TypeError, "phone_types must be a tuple of %d members", TYPE_MEMBERS);
        return -1;
    }
    self->number_type = Py_NewRef(number_type);
    self->types = Py_NewRef(types);
    return 0;
}

static int fill_config(PhoneConfigObject *self, PyObject *spec) {
    th_phone_config *config = &self->config;
    if (parse_regions(PyTuple_GET_ITEM(spec, 0), config) < 0 ||
        parse_flag(PyTuple_GET_ITEM(spec, 1), "require_valid", &config->require_valid) < 0 ||
        parse_flag(PyTuple_GET_ITEM(spec, 2), "require_separators", &config->require_separators) < 0 ||
        parse_flag(PyTuple_GET_ITEM(spec, 3), "skip_card_numbers", &config->skip_card_numbers) < 0 ||
        parse_flag(PyTuple_GET_ITEM(spec, 4), "require_national_prefix", &config->require_national_prefix) < 0 ||
        parse_grouping(PyTuple_GET_ITEM(spec, 5), config, &config->grouping) < 0 ||
        parse_type_mask(PyTuple_GET_ITEM(spec, 6), config, &config->type_mask) < 0 ||
        parse_labels(PyTuple_GET_ITEM(spec, 7), self) < 0 ||
        parse_classes(PyTuple_GET_ITEM(spec, 8), PyTuple_GET_ITEM(spec, 9), self) < 0 ||
        parse_flag(PyTuple_GET_ITEM(spec, 10), "parsing_extensions", &config->parsing_extensions) < 0) {
        return -1;
    }
    th_phone_config_floor(config);
    return 0;
}

/* _phone_config_compile(spec) -> _PhoneConfig, spec = (regions, require_valid, require_separators,
   skip_card_numbers, require_national_prefix, grouping, type_mask, labels, phone_number_type, phone_types,
   parsing_extensions). */
PyObject *turbohtml_phone_config_compile(PyObject *module, PyObject *spec) {
    if (!PyTuple_Check(spec) || PyTuple_GET_SIZE(spec) != SPEC_ITEMS) {
        PyErr_Format(PyExc_TypeError, "phone spec must be a tuple of %d items", SPEC_ITEMS);
        return NULL;
    }
    module_state *state = PyModule_GetState(module);
    PhoneConfigObject *self = PyObject_GC_New(PhoneConfigObject, (PyTypeObject *)state->phone_config_type);
    if (self == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return NULL;    /* GCOVR_EXCL_LINE */
    }
    memset(&self->config, 0, sizeof(self->config));
    self->labels = NULL;
    self->label_bytes = NULL;
    self->number_type = NULL;
    self->types = NULL;
    if (fill_config(self, spec) < 0) {
        Py_DECREF(self);
        return NULL;
    }
    PyObject_GC_Track(self);
    return (PyObject *)self;
}

PyObject *turbohtml_phone_number_new(const PhoneConfigObject *config, const th_phone_match *match) {
    PyObject *nsn = PyUnicode_FromStringAndSize(match->nsn, match->nsn_len);
    PyObject *extension = match->ext_len ? PyUnicode_FromStringAndSize(match->ext, match->ext_len) : Py_NewRef(Py_None);
    PyObject *region;
    if (match->region < 0) {
        region = Py_NewRef(Py_None);
    } else {
        size_t code_len;
        const char *code = th_phone_region_code(match->region, &code_len);
        region = PyUnicode_FromStringAndSize(code, (Py_ssize_t)code_len);
    }
    PyObject *country_code = PyLong_FromLong(match->country_code);
    PyObject *number = NULL;
    if (nsn != NULL && extension != NULL && region != NULL && country_code != NULL) { /* GCOVR_EXCL_BR_LINE: alloc */
        PyObject *member = PyTuple_GET_ITEM(config->types, match->type);
        number = PyObject_CallMethod(config->number_type, "_from_native", "OOOOO", country_code, nsn, extension, region,
                                     member);
        if (number != NULL && !PyObject_TypeCheck(number, (PyTypeObject *)config->number_type)) {
            PyErr_Format(PyExc_TypeError, "_from_native must return a %s instance",
                         ((PyTypeObject *)config->number_type)->tp_name);
            Py_DECREF(number);
            number = NULL;
        }
    }
    Py_XDECREF(nsn);
    Py_XDECREF(extension);
    Py_XDECREF(region);
    Py_XDECREF(country_code);
    return number;
}

static int parse_national_number(PyObject *value, const char **digits, Py_ssize_t *length) {
    if (!PyUnicode_CheckExact(value)) {
        PyErr_SetString(PyExc_TypeError, "national_number must be str");
        return -1;
    }
    *digits = PyUnicode_AsUTF8AndSize(value, length);
    if (*digits == NULL) {
        return -1;
    }
    if (*length < 2 || *length > MAX_NSN) {
        PyErr_Format(PyExc_ValueError, "national_number must be 2-%d digits", MAX_NSN);
        return -1;
    }
    for (Py_ssize_t offset = 0; offset < *length; offset++) {
        if ((*digits)[offset] < '0' || (*digits)[offset] > '9') {
            PyErr_SetString(PyExc_ValueError, "national_number must be ASCII digits");
            return -1;
        }
    }
    return 0;
}

/* _phone_number_check(country_code, national_number, region, type_index) -> None: ValueError when the tables never
   produce this PhoneNumber value. */
PyObject *turbohtml_phone_number_check(PyObject *Py_UNUSED(module), PyObject *args) {
    PyObject *country_code;
    PyObject *national_number;
    PyObject *region;
    PyObject *type_index;
    if (!PyArg_ParseTuple(args, "OOOO:_phone_number_check", &country_code, &national_number, &region, &type_index)) {
        return NULL;
    }
    if (!PyLong_CheckExact(country_code) || !PyLong_CheckExact(type_index)) {
        PyErr_SetString(PyExc_TypeError, "country_code and type_index must be int");
        return NULL;
    }
    if (region != Py_None && !PyUnicode_CheckExact(region)) {
        PyErr_SetString(PyExc_TypeError, "region must be str or None");
        return NULL;
    }
    long code = PyLong_AsLong(country_code);
    if (code < 1 || code > 999) {
        PyErr_SetString(PyExc_ValueError, "country_code must be between 1 and 999");
        return NULL;
    }
    long type = PyLong_AsLong(type_index);
    if (type < 0 || type > TH_PHONE_UNKNOWN) {
        PyErr_SetString(PyExc_ValueError, "type_index must be between 0 and 11");
        return NULL;
    }
    const char *digits;
    Py_ssize_t length;
    if (parse_national_number(national_number, &digits, &length) < 0) {
        return NULL;
    }
    const char *region_text = NULL;
    Py_ssize_t region_length = 0;
    if (region != Py_None) {
        region_text = PyUnicode_AsUTF8AndSize(region, &region_length);
        if (region_text == NULL) {
            return NULL;
        }
    }
    switch (
        th_phone_number_check((unsigned)code, digits, (size_t)length, region_text, (size_t)region_length, (int)type)) {
    case TH_PHONE_CHECK_OK:
        Py_RETURN_NONE;
    case TH_PHONE_CHECK_COUNTRY_CODE:
        return PyErr_Format(PyExc_ValueError, "country code %ld is not assigned", code);
    case TH_PHONE_CHECK_REGION:
        return PyErr_Format(PyExc_ValueError, "region %R is not in country code %ld", region, code);
    default:
        return PyErr_Format(PyExc_ValueError, "no number +%ld%s has region %R and type index %ld", code, digits, region,
                            type);
    }
}

/* _phone_number_format(country_code, national_number, extension, style) -> str: the number written the way
   libphonenumber's formatNumber writes it, `style` indexing PhoneFormat's members. */
PyObject *turbohtml_phone_number_format(PyObject *Py_UNUSED(module), PyObject *args) {
    PyObject *country_code;
    PyObject *national_number;
    PyObject *extension;
    PyObject *style;
    if (!PyArg_ParseTuple(args, "OOOO:_phone_number_format", &country_code, &national_number, &extension, &style)) {
        return NULL;
    }
    if (!PyLong_CheckExact(country_code) || !PyLong_CheckExact(style)) {
        PyErr_SetString(PyExc_TypeError, "country_code and style must be int");
        return NULL;
    }
    if (extension != Py_None && !PyUnicode_CheckExact(extension)) {
        PyErr_SetString(PyExc_TypeError, "extension must be str or None");
        return NULL;
    }
    long code = PyLong_AsLong(country_code);
    if (code < 1 || code > 999) {
        PyErr_SetString(PyExc_ValueError, "country_code must be between 1 and 999");
        return NULL;
    }
    long style_index = PyLong_AsLong(style);
    if (style_index < TH_PHONE_STYLE_E164 || style_index > TH_PHONE_STYLE_RFC3966) {
        PyErr_SetString(PyExc_ValueError, "style must be between 0 and 3");
        return NULL;
    }
    const char *digits;
    Py_ssize_t length;
    if (parse_national_number(national_number, &digits, &length) < 0) {
        return NULL;
    }
    const char *ext = "";
    Py_ssize_t ext_length = 0;
    if (extension != Py_None) {
        ext = PyUnicode_AsUTF8AndSize(extension, &ext_length);
        if (ext == NULL) {
            return NULL;
        }
        if (ext_length < 1 || ext_length > TH_PHONE_MAX_EXTENSION) {
            PyErr_Format(PyExc_ValueError, "extension must be 1-%d characters", TH_PHONE_MAX_EXTENSION);
            return NULL;
        }
    }
    char out[TH_PHONE_FORMAT_CAPACITY];
    size_t written = th_phone_format_number((unsigned)code, digits, (size_t)length, ext, (size_t)ext_length,
                                            (enum th_phone_style)style_index, out);
    return PyUnicode_FromStringAndSize(out, (Py_ssize_t)written);
}

static uint32_t read_str(const void *text, size_t index) {
    return PyUnicode_READ_CHAR((PyObject *)text, (Py_ssize_t)index);
}

/* _phone_parse(config, text) -> PhoneNumber | None: the one number `text` holds. */
PyObject *turbohtml_phone_parse(PyObject *module, PyObject *args) {
    PyObject *config;
    PyObject *text;
    if (!PyArg_ParseTuple(args, "OO:_phone_parse", &config, &text)) {
        return NULL;
    }
    if (!turbohtml_phone_config_check(module, config)) {
        PyErr_SetString(PyExc_TypeError, "config must be a _PhoneConfig");
        return NULL;
    }
    if (!PyUnicode_CheckExact(text)) {
        PyErr_SetString(PyExc_TypeError, "text must be str");
        return NULL;
    }
    th_phone_match match;
    if (!th_phone_parse(read_str, text, 0, (size_t)PyUnicode_GET_LENGTH(text), &((PhoneConfigObject *)config)->config,
                        &match)) {
        Py_RETURN_NONE;
    }
    return turbohtml_phone_number_new((PhoneConfigObject *)config, &match);
}

int phone_register(PyObject *module, module_state *state) {
    state->phone_config_type = PyType_FromModuleAndSpec(module, &phone_config_spec, NULL);
    if (state->phone_config_type == NULL) { /* GCOVR_EXCL_BR_LINE: allocation failure cannot be forced from a test */
        return -1;                          /* GCOVR_EXCL_LINE */
    }
    return 0;
}
