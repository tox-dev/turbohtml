/* Local file loading for stylesheet imports and schema includes, confined to an optional root directory.

   xsl:import (query/xslt.c) and RELAX NG include/externalRef (validate/relaxng.h) read referenced files through one
   policy, so both reject remote URLs, UNC paths, symlinks out of the root and non-regular files the same way, and
   both draw on the same allowance of files and bytes. */

#ifndef TURBOHTML_CORE_RESOURCE_H
#define TURBOHTML_CORE_RESOURCE_H

#include "core/common.h"

#ifdef _WIN32
#include <windows.h>
#endif

/* What one policy, and so one compile, may read. Each reference costs a parsed tree, a copy grafted into the compiled
   document and its compiled form, tens of times the file's size and kilobytes for an empty file, and a document may
   name one file any number of times, so without a cap a short stylesheet or schema allocates gigabytes. */
#define TH_RESOURCE_MAX_FILES 1024
#define TH_RESOURCE_MAX_BYTES ((size_t)8 << 20)

typedef struct {
    PyObject *path_type;
    PyObject *urlparse;
    PyObject *url2pathname;
    PyObject *root;
    const char *what;      /* the referencing construct that leads each error message, such as "xsl:import" */
    const char *root_name; /* the keyword argument that set root, named when a path escapes it */
    size_t files;          /* the references read so far, at most TH_RESOURCE_MAX_FILES */
    size_t bytes;          /* their total size, at most TH_RESOURCE_MAX_BYTES */
#ifdef _WIN32
    HANDLE root_handle;
    wchar_t *root_final;
    size_t root_final_len;
#else
    int root_fd;
#endif
} th_resource_policy;

/* Load pathlib and urllib once and open root (None for no confinement); 0, or -1 with an exception set. */
int th_resource_policy_init(th_resource_policy *policy, const char *what, const char *root_name, PyObject *root);
void th_resource_policy_clear(th_resource_policy *policy);
/* A pathlib.Path for a local path or file URL; name labels value in the error a remote or UNC location raises. */
PyObject *th_resource_path(th_resource_policy *policy, PyObject *value, const char *name);
PyObject *th_resource_resolve(PyObject *path);
/* 0 when the resolved path lies inside the root, or -1 with a ValueError naming unresolved, the path as referenced. */
int th_resource_check_root(th_resource_policy *policy, PyObject *path, PyObject *unresolved);
/* The UTF-8 text of a regular file, opened beneath the root without following symlinks when a root is set; NULL with
   a ValueError once the policy's reads pass TH_RESOURCE_MAX_FILES or TH_RESOURCE_MAX_BYTES. */
PyObject *th_resource_read_text(th_resource_policy *policy, PyObject *path);

#endif /* TURBOHTML_CORE_RESOURCE_H */
