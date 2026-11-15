#include "core/resource.h"
#include "core/vec.h"

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <string.h>

#include <sys/stat.h>

#ifdef _WIN32
#include <io.h>
#include <wchar.h>
#else
#include <unistd.h>
#endif

static void resource_descriptor_close(int descriptor) {
#ifdef _WIN32
    _close(descriptor);
#else
    close(descriptor);
#endif
}

static Py_ssize_t resource_descriptor_read(int descriptor, char *buffer, size_t size) {
#ifdef _WIN32
    int count;
    Py_BEGIN_ALLOW_THREADS count = _read(descriptor, buffer, (unsigned int)size);
#else
    ssize_t count;
    Py_BEGIN_ALLOW_THREADS count = read(descriptor, buffer, size);
#endif
    Py_END_ALLOW_THREADS return (Py_ssize_t)count;
}

void th_resource_policy_clear(th_resource_policy *policy) {
    Py_DECREF(policy->path_type);
    Py_DECREF(policy->urlparse);
    Py_DECREF(policy->url2pathname);
    Py_XDECREF(policy->root);
#ifdef _WIN32
    if (policy->root_handle != INVALID_HANDLE_VALUE) {
        CloseHandle(policy->root_handle);
    }
    PyMem_Free(policy->root_final);
#else
    if (policy->root_fd >= 0) {
        resource_descriptor_close(policy->root_fd);
    }
#endif
}

static int resource_is_unc(PyObject *text);

PyObject *th_resource_path(th_resource_policy *policy, PyObject *value, const char *name) {
    if (!PyUnicode_Check(value)) {
        return PyObject_CallOneArg(policy->path_type, value);
    }
    PyObject *parsed = PyObject_CallOneArg(policy->urlparse, value);
    if (parsed == NULL) {
        return NULL;
    }
    PyObject *scheme = PyObject_GetAttrString(parsed, "scheme");
    PyObject *netloc = PyObject_GetAttrString(parsed, "netloc");
    PyObject *url_path = PyObject_GetAttrString(parsed, "path");
    /* GCOVR_EXCL_BR_START: ParseResult guarantees the scheme, netloc and path attributes. */
    if (scheme == NULL || netloc == NULL || url_path == NULL) {
        Py_XDECREF(scheme);   /* GCOVR_EXCL_LINE */
        Py_XDECREF(netloc);   /* GCOVR_EXCL_LINE */
        Py_XDECREF(url_path); /* GCOVR_EXCL_LINE */
        Py_DECREF(parsed);    /* GCOVR_EXCL_LINE */
        return NULL;          /* GCOVR_EXCL_LINE */
    }
    /* GCOVR_EXCL_BR_STOP */
    int is_file = PyUnicode_CompareWithASCIIString(scheme, "file") == 0;
    int has_scheme = PyUnicode_GET_LENGTH(scheme) != 0;
    int windows_drive = PyUnicode_GET_LENGTH(value) > 2 && PyUnicode_ReadChar(value, 1) == ':' &&
                        (PyUnicode_ReadChar(value, 2) == '\\' || PyUnicode_ReadChar(value, 2) == '/');
    PyObject *local = NULL;
    if (is_file) {
        int local_host =
            PyUnicode_GET_LENGTH(netloc) == 0 || PyUnicode_CompareWithASCIIString(netloc, "localhost") == 0;
        if (!local_host) {
            PyErr_Format(PyExc_ValueError, "%s %s file URL must point to a local path", policy->what, name);
        } else if (resource_is_unc(url_path)) {
            PyErr_Format(PyExc_ValueError, "%s %s must not resolve to a UNC path: %S", policy->what, name, value);
        } else {
            local = PyObject_CallOneArg(policy->url2pathname, url_path);
        }
    } else if ((has_scheme && !windows_drive) || PyUnicode_GET_LENGTH(netloc) != 0) {
        PyErr_Format(PyExc_ValueError, "%s %s must be a local path or file URL", policy->what, name);
    } else if (resource_is_unc(value)) {
        PyErr_Format(PyExc_ValueError, "%s %s must not resolve to a UNC path: %S", policy->what, name, value);
    } else {
        local = PyObject_CallOneArg(policy->url2pathname, value);
    }
    Py_DECREF(url_path);
    Py_DECREF(netloc);
    Py_DECREF(scheme);
    Py_DECREF(parsed);
    if (local == NULL) {
        return NULL;
    }
    /* url2pathname decodes percent escapes and drops tabs and newlines, so a UNC path can only appear in its output */
    if (resource_is_unc(local)) {
        PyErr_Format(PyExc_ValueError, "%s %s must not resolve to a UNC path: %S", policy->what, name, value);
        Py_DECREF(local);
        return NULL;
    }
    PyObject *path = PyObject_CallOneArg(policy->path_type, local);
    Py_DECREF(local);
    return path;
}

/* url2pathname maps \\host, file:////host and ////host alike to a UNC path, which CreateFileW opens over SMB on an
   arbitrary host; rejected on every platform. */
static int resource_is_unc(PyObject *text) {
    if (PyUnicode_GET_LENGTH(text) < 2) {
        return 0;
    }
    Py_UCS4 first = PyUnicode_ReadChar(text, 0);
    if (first != '\\' && first != '/') {
        return 0;
    }
    Py_UCS4 second = PyUnicode_ReadChar(text, 1);
    return second == '\\' || second == '/';
}

/* Path.resolve, Path.is_relative_to and Path.relative_to each rebuild a string per component, so their cost is
   quadratic in the length of a path an href sets; no system call accepts one longer than this. */
#ifdef _WIN32
#define RESOURCE_PATH_MAX 32767
#elif defined(PATH_MAX)
#define RESOURCE_PATH_MAX PATH_MAX
#else
#define RESOURCE_PATH_MAX 4096
#endif

PyObject *th_resource_resolve(PyObject *path) {
    PyObject *text = PyObject_Str(path);
    if (text == NULL) { /* GCOVR_EXCL_BR_LINE: pathlib.Path.__str__ only allocates */
        return NULL;    /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t length = PyUnicode_GET_LENGTH(text);
    Py_DECREF(text);
    if (length > RESOURCE_PATH_MAX) {
        errno = ENAMETOOLONG;
        return PyErr_SetFromErrnoWithFilenameObject(PyExc_OSError, path);
    }
    return PyObject_CallMethod(path, "resolve", NULL);
}

int th_resource_check_root(th_resource_policy *policy, PyObject *path, PyObject *unresolved) {
    if (policy->root == NULL) {
        return 0;
    }
    PyObject *inside = PyObject_CallMethod(path, "is_relative_to", "O", policy->root);
    if (inside == NULL) { /* GCOVR_EXCL_BR_LINE: Path.is_relative_to returns bool or allocates */
        return -1;        /* GCOVR_EXCL_LINE */
    }
    int allowed = PyObject_IsTrue(inside);
    Py_DECREF(inside);
    if (allowed < 0) { /* GCOVR_EXCL_BR_LINE: bool truth testing cannot fail */
        return -1;     /* GCOVR_EXCL_LINE */
    }
    if (!allowed) {
        /* path has followed every symlink the reference crosses, so naming it would reveal where a link outside the
           root points */
        PyErr_Format(PyExc_ValueError, "%s path escapes %s: %S", policy->what, policy->root_name, unresolved);
        return -1;
    }
    return 0;
}

#ifdef _WIN32
static HANDLE resource_windows_open(PyObject *path, DWORD access, DWORD share, DWORD flags) {
    PyObject *text = PyObject_Str(path);
    if (text == NULL) {
        return INVALID_HANDLE_VALUE;
    }
    wchar_t *wide = PyUnicode_AsWideCharString(text, NULL);
    Py_DECREF(text);
    if (wide == NULL) {
        return INVALID_HANDLE_VALUE;
    }
    HANDLE handle;
    Py_BEGIN_ALLOW_THREADS handle = CreateFileW(wide, access, share, NULL, OPEN_EXISTING, flags, NULL);
    Py_END_ALLOW_THREADS PyMem_Free(wide);
    if (handle == INVALID_HANDLE_VALUE) {
        PyErr_SetExcFromWindowsErrWithFilenameObject(PyExc_OSError, 0, path);
    }
    return handle;
}

static wchar_t *resource_windows_final_path(HANDLE handle, size_t *length) {
    DWORD size;
    Py_BEGIN_ALLOW_THREADS size = GetFinalPathNameByHandleW(handle, NULL, 0, FILE_NAME_NORMALIZED | VOLUME_NAME_DOS);
    Py_END_ALLOW_THREADS if (size == 0) {
        PyErr_SetFromWindowsErr(0);
        return NULL;
    }
    wchar_t *path = PyMem_Malloc(((size_t)size + 1) * sizeof(*path));
    if (path == NULL) {   /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
        return NULL;      /* GCOVR_EXCL_LINE */
    }
    DWORD written;
    Py_BEGIN_ALLOW_THREADS written =
        GetFinalPathNameByHandleW(handle, path, size + 1, FILE_NAME_NORMALIZED | VOLUME_NAME_DOS);
    Py_END_ALLOW_THREADS if (written == 0 || written > size) {
        PyMem_Free(path);
        PyErr_SetFromWindowsErr(0);
        return NULL;
    }
    *length = written;
    return path;
}

static int resource_windows_path_inside(th_resource_policy *policy, const wchar_t *path, size_t length) {
    size_t root_length = policy->root_final_len;
    while (root_length > 0 &&
           (policy->root_final[root_length - 1] == L'\\' || policy->root_final[root_length - 1] == L'/')) {
        root_length--;
    }
    if (length < root_length || wcsncmp(policy->root_final, path, root_length) != 0) {
        return 0;
    }
    return length == root_length || path[root_length] == L'\\' || path[root_length] == L'/';
}

static int resource_open_root(th_resource_policy *policy) {
    policy->root_handle = resource_windows_open(policy->root, FILE_READ_ATTRIBUTES, FILE_SHARE_READ | FILE_SHARE_WRITE,
                                                FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT);
    if (policy->root_handle == INVALID_HANDLE_VALUE) {
        return -1;
    }
    policy->root_final = resource_windows_final_path(policy->root_handle, &policy->root_final_len);
    return policy->root_final == NULL ? -1 : 0;
}

static int resource_windows_descriptor(HANDLE handle, PyObject *path) {
    int descriptor = _open_osfhandle((intptr_t)handle, _O_RDONLY | _O_BINARY);
    if (descriptor < 0) {
        CloseHandle(handle);
        PyErr_SetFromErrnoWithFilenameObject(PyExc_OSError, path);
    }
    return descriptor;
}

/* CreateFileW opens a directory only with FILE_FLAG_BACKUP_SEMANTICS and otherwise fails with ERROR_ACCESS_DENIED
   (CreateFileW, "Directories"), so without it a directory target raised OSError where POSIX reaches the regular-file
   check. CPython's os.stat opens with the same flag. */
static const DWORD RESOURCE_OPEN_FLAGS = FILE_ATTRIBUTE_NORMAL | FILE_FLAG_BACKUP_SEMANTICS;

static int resource_open_file(th_resource_policy *policy, PyObject *path) {
    HANDLE handle = resource_windows_open(path, GENERIC_READ | FILE_READ_ATTRIBUTES,
                                          FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, RESOURCE_OPEN_FLAGS);
    if (handle == INVALID_HANDLE_VALUE) {
        return -1;
    }
    size_t final_length;
    wchar_t *final_path = resource_windows_final_path(handle, &final_length);
    if (final_path == NULL) {
        CloseHandle(handle);
        return -1;
    }
    int inside = resource_windows_path_inside(policy, final_path, final_length);
    PyMem_Free(final_path);
    if (!inside) {
        CloseHandle(handle);
        PyErr_Format(PyExc_ValueError, "%s path escapes %s: %S", policy->what, policy->root_name, path);
        return -1;
    }
    return resource_windows_descriptor(handle, path);
}

static int resource_open_path(PyObject *path) {
    HANDLE handle = resource_windows_open(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                                          RESOURCE_OPEN_FLAGS);
    return handle == INVALID_HANDLE_VALUE ? -1 : resource_windows_descriptor(handle, path);
}
#else
static PyObject *resource_fs_bytes(PyObject *path) {
    PyObject *value = PyObject_Str(path);
    if (value == NULL) { /* GCOVR_EXCL_BR_LINE: pathlib.Path.__str__ only allocates */
        return NULL;     /* GCOVR_EXCL_LINE */
    }
    PyObject *bytes = PyUnicode_EncodeFSDefault(value);
    Py_DECREF(value);
    return bytes;
}

static int resource_open_beneath(const th_resource_policy *policy, int anchor, PyObject *path, PyObject *error_path,
                                 int require_directory) {
    PyObject *bytes = resource_fs_bytes(path);
    if (bytes == NULL) { /* GCOVR_EXCL_BR_LINE: pathlib paths encode unless allocation fails */
        return -1;       /* GCOVR_EXCL_LINE */
    }
    Py_ssize_t length = PyBytes_GET_SIZE(bytes);
    char *parts = PyMem_Malloc((size_t)length + 1);
    if (parts == NULL) {  /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
        Py_DECREF(bytes); /* GCOVR_EXCL_LINE */
        PyErr_NoMemory(); /* GCOVR_EXCL_LINE */
        return -1;        /* GCOVR_EXCL_LINE */
    }
    memcpy(parts, PyBytes_AS_STRING(bytes), (size_t)length);
    parts[length] = '\0';
    Py_DECREF(bytes);
    int descriptor;
    do {
        descriptor = fcntl(anchor, F_DUPFD_CLOEXEC, 0);
    } while (descriptor < 0 && errno == EINTR); /* GCOVR_EXCL_BR_LINE: requires a signal during fcntl */
    if (descriptor < 0) {  /* GCOVR_EXCL_BR_LINE: requires process-wide descriptor exhaustion */
        PyMem_Free(parts); /* GCOVR_EXCL_LINE */
        PyErr_SetFromErrnoWithFilenameObject(PyExc_OSError, error_path); /* GCOVR_EXCL_LINE */
        return -1;                                                       /* GCOVR_EXCL_LINE */
    }
    char *cursor = parts;
    while (*cursor == '/') {
        cursor++;
    }
    while (*cursor != '\0') {
        char *separator = strchr(cursor, '/');
        if (separator != NULL) {
            *separator = '\0';
        }
        int last_component = separator == NULL;
        int flags = O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK;
        if (!last_component || require_directory) {
            flags |= O_DIRECTORY;
        }
        int opened_descriptor;
        do {
            Py_BEGIN_ALLOW_THREADS opened_descriptor = openat(descriptor, cursor, flags);
            Py_END_ALLOW_THREADS
        } while (opened_descriptor < 0 && errno == EINTR); /* GCOVR_EXCL_BR_LINE: requires a signal during openat */
        if (opened_descriptor < 0) {
            int error = errno;
            resource_descriptor_close(descriptor);
            PyMem_Free(parts);
            if (error == ELOOP || error == ENOTDIR) {
                PyErr_Format(PyExc_ValueError, "%s path escapes %s: %S", policy->what, policy->root_name, error_path);
            } else {
                errno = error;
                PyErr_SetFromErrnoWithFilenameObject(PyExc_OSError, error_path);
            }
            return -1;
        }
        resource_descriptor_close(descriptor);
        descriptor = opened_descriptor;
        if (last_component) {
            break;
        }
        cursor = separator + 1;
    }
    PyMem_Free(parts);
    return descriptor;
}

static int resource_open_root(th_resource_policy *policy) {
    int anchor = open("/", O_RDONLY | O_CLOEXEC | O_DIRECTORY);
    if (anchor < 0) {                      /* GCOVR_EXCL_BR_LINE: supported POSIX systems expose the filesystem root */
        PyErr_SetFromErrno(PyExc_OSError); /* GCOVR_EXCL_LINE */
        return -1;                         /* GCOVR_EXCL_LINE */
    }
    policy->root_fd = resource_open_beneath(policy, anchor, policy->root, policy->root, 1);
    resource_descriptor_close(anchor);
    return policy->root_fd < 0 ? -1 : 0;
}

static int resource_open_path(PyObject *path) {
    PyObject *bytes = resource_fs_bytes(path);
    if (bytes == NULL) { /* GCOVR_EXCL_BR_LINE: pathlib paths encode unless allocation fails */
        return -1;       /* GCOVR_EXCL_LINE */
    }
    int descriptor;
    do {
        Py_BEGIN_ALLOW_THREADS descriptor = open(PyBytes_AS_STRING(bytes), O_RDONLY | O_CLOEXEC | O_NONBLOCK);
        Py_END_ALLOW_THREADS
    } while (descriptor < 0 && errno == EINTR); /* GCOVR_EXCL_BR_LINE: requires a signal during open */
    if (descriptor < 0) {
        PyErr_SetFromErrnoWithFilenameObject(PyExc_OSError, path);
    }
    Py_DECREF(bytes);
    return descriptor;
}

static int resource_open_file(th_resource_policy *policy, PyObject *path) {
    PyObject *relative = PyObject_CallMethod(path, "relative_to", "O", policy->root);
    if (relative == NULL) { /* GCOVR_EXCL_BR_LINE: th_resource_check_root established containment */
        return -1;          /* GCOVR_EXCL_LINE */
    }
    int descriptor = resource_open_beneath(policy, policy->root_fd, relative, path, 0);
    Py_DECREF(relative);
    return descriptor;
}
#endif

static int resource_descriptor_is_regular(int descriptor) {
#ifdef _WIN32
    /* a disk handle without FILE_ATTRIBUTE_DIRECTORY is a regular file; pipes and character devices report another
       GetFileType */
    HANDLE handle = (HANDLE)_get_osfhandle(descriptor);
    BY_HANDLE_FILE_INFORMATION info;
    return GetFileType(handle) == FILE_TYPE_DISK && GetFileInformationByHandle(handle, &info) &&
           !(info.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY);
#else
    struct stat info = {0};
    (void)fstat(descriptor, &info);
    return S_ISREG(info.st_mode);
#endif
}

static PyObject *resource_read_descriptor(th_resource_policy *policy, int descriptor, PyObject *path) {
    /* a FIFO or a device such as /dev/zero has no end of file, so reading one hangs or exhausts memory */
    if (!resource_descriptor_is_regular(descriptor)) {
        resource_descriptor_close(descriptor);
        PyErr_Format(PyExc_ValueError, "%s target is not a regular file: %S", policy->what, path);
        return NULL;
    }
    char *data = NULL;
    size_t length = 0;
    size_t capacity = 0;
    for (;;) {
        if (length == capacity) {
            size_t grown_capacity;
            size_t grown_bytes;
            /* GCOVR_EXCL_BR_START: address space cannot hold the input. */
            if (!th_grow_cap(length + 1, capacity, 65536, sizeof(*data), &grown_capacity, &grown_bytes)) {
                PyMem_Free(data);                      /* GCOVR_EXCL_LINE */
                PyErr_NoMemory();                      /* GCOVR_EXCL_LINE */
                resource_descriptor_close(descriptor); /* GCOVR_EXCL_LINE */
                return NULL;                           /* GCOVR_EXCL_LINE */
            }
            /* GCOVR_EXCL_BR_STOP */
            char *grown = PyMem_Realloc(data, grown_bytes);
            if (grown == NULL) {                       /* GCOVR_EXCL_BR_LINE: allocation cannot be forced */
                PyMem_Free(data);                      /* GCOVR_EXCL_LINE */
                PyErr_NoMemory();                      /* GCOVR_EXCL_LINE */
                resource_descriptor_close(descriptor); /* GCOVR_EXCL_LINE */
                return NULL;                           /* GCOVR_EXCL_LINE */
            }
            data = grown;
            capacity = grown_capacity;
        }
        size_t remaining = capacity - length;
        Py_ssize_t count = resource_descriptor_read(descriptor, data + length, remaining < 65536 ? remaining : 65536);
        if (count < 0 && errno == EINTR) { /* GCOVR_EXCL_BR_LINE: requires a signal during the read syscall */
            continue;                      /* GCOVR_EXCL_LINE */
        }
        if (count < 0) { /* GCOVR_EXCL_BR_LINE: a regular file read fails only on an I/O error a test cannot force */
            int error = errno;                                         /* GCOVR_EXCL_LINE */
            PyMem_Free(data);                                          /* GCOVR_EXCL_LINE */
            resource_descriptor_close(descriptor);                     /* GCOVR_EXCL_LINE */
            errno = error;                                             /* GCOVR_EXCL_LINE */
            PyErr_SetFromErrnoWithFilenameObject(PyExc_OSError, path); /* GCOVR_EXCL_LINE */
            return NULL;                                               /* GCOVR_EXCL_LINE */
        }
        if (count == 0) {
            break;
        }
        length += (size_t)count;
        if (length > TH_RESOURCE_MAX_BYTES - policy->bytes) {
            PyMem_Free(data);
            resource_descriptor_close(descriptor);
            PyErr_Format(PyExc_ValueError, "%s references exceed the limit of %zu MiB: %S", policy->what,
                         TH_RESOURCE_MAX_BYTES >> 20, path);
            return NULL;
        }
    }
    policy->bytes += length;
    resource_descriptor_close(descriptor);
    PyObject *text = PyUnicode_DecodeUTF8(data, (Py_ssize_t)length, "strict");
    PyMem_Free(data);
    return text;
}

PyObject *th_resource_read_text(th_resource_policy *policy, PyObject *path) {
    if (policy->files == TH_RESOURCE_MAX_FILES) {
        PyErr_Format(PyExc_ValueError, "%s references exceed the limit of %d files: %S", policy->what,
                     TH_RESOURCE_MAX_FILES, path);
        return NULL;
    }
    policy->files++;
    int descriptor = policy->root == NULL ? resource_open_path(path) : resource_open_file(policy, path);
    if (descriptor < 0) {
        return NULL;
    }
    return resource_read_descriptor(policy, descriptor, path);
}

int th_resource_policy_init(th_resource_policy *policy, const char *what, const char *root_name, PyObject *root) {
    memset(policy, 0, sizeof(*policy));
    policy->what = what;
    policy->root_name = root_name;
#ifdef _WIN32
    policy->root_handle = INVALID_HANDLE_VALUE;
#else
    policy->root_fd = -1;
#endif
    PyObject *pathlib = PyImport_ImportModule("pathlib");
    PyObject *parse = PyImport_ImportModule("urllib.parse");
    PyObject *request = PyImport_ImportModule("urllib.request");
    /* Bundled standard-library imports fail only on allocation. */
    /* GCOVR_EXCL_BR_START */
    if (pathlib == NULL || parse == NULL || request == NULL) {
        Py_XDECREF(pathlib); /* GCOVR_EXCL_LINE */
        Py_XDECREF(parse);   /* GCOVR_EXCL_LINE */
        Py_XDECREF(request); /* GCOVR_EXCL_LINE */
        return -1;           /* GCOVR_EXCL_LINE */
    }
    /* GCOVR_EXCL_BR_STOP */
    policy->path_type = PyObject_GetAttrString(pathlib, "Path");
    policy->urlparse = PyObject_GetAttrString(parse, "urlparse");
    policy->url2pathname = PyObject_GetAttrString(request, "url2pathname");
    Py_DECREF(request);
    Py_DECREF(parse);
    Py_DECREF(pathlib);
    /* Bundled modules expose these attributes. */
    /* GCOVR_EXCL_BR_START */
    if (policy->path_type == NULL || policy->urlparse == NULL || policy->url2pathname == NULL) {
        Py_XDECREF(policy->path_type);    /* GCOVR_EXCL_LINE */
        Py_XDECREF(policy->urlparse);     /* GCOVR_EXCL_LINE */
        Py_XDECREF(policy->url2pathname); /* GCOVR_EXCL_LINE */
        return -1;                        /* GCOVR_EXCL_LINE */
    }
    /* GCOVR_EXCL_BR_STOP */
    if (root != Py_None) {
        PyObject *root_path = PyObject_CallOneArg(policy->path_type, root);
        if (root_path == NULL) {
            th_resource_policy_clear(policy);
            return -1;
        }
        policy->root = th_resource_resolve(root_path);
        Py_DECREF(root_path);
        if (policy->root == NULL) {
            th_resource_policy_clear(policy);
            return -1;
        }
        if (resource_open_root(policy) < 0) {
            th_resource_policy_clear(policy);
            return -1;
        }
    }
    return 0;
}
