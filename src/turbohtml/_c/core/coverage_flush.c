#include "core/common.h"

/* Both runtimes link into this module and resolve here even where the archive hides them (GCC's libgcov). */
extern void __gcov_dump(void);
extern void __gcov_reset(void);

/* gcov writes counters from an exit-time destructor, which a SIGKILL or os._exit skips. Dumping and then zeroing
   leaves the exit-time write to add only what runs after this call, so nothing is counted twice. */
PyObject *turbohtml_coverage_flush(PyObject *module, PyObject *Py_UNUSED(ignored)) {
    (void)module;
    __gcov_dump();
    __gcov_reset();
    Py_RETURN_NONE;
}
