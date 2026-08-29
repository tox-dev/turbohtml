/* The compiled phone-detection configuration the link scanner carries, and its two private bindings. */
#ifndef TURBOHTML_CLEAN_PHONE_BINDING_H
#define TURBOHTML_CLEAN_PHONE_BINDING_H

#include "clean/phone.h"
#include "core/common.h"
#include "tokenizer/binding.h"

typedef struct {
    PyObject_HEAD th_phone_config config;
    th_phone_label *labels; /* the label table `config.labels` points at, owned */
    char *label_bytes;      /* the ASCII text those labels point into, owned */
    PyObject *number_type;  /* PhoneNumber, whose _from_native builds a detected number */
    PyObject *types;        /* the twelve PhoneType members, indexed by enum th_phone_type */
} PhoneConfigObject;

int turbohtml_phone_config_check(PyObject *module, PyObject *object);

PyObject *turbohtml_phone_number_new(const PhoneConfigObject *config, const th_phone_match *match);

int phone_register(PyObject *module, module_state *state);

#endif /* TURBOHTML_CLEAN_PHONE_BINDING_H */
