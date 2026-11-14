#include "core/common.h"
#include "core/work_steps.h"

#if defined(_MSC_VER)
__declspec(thread) uint64_t th_work_steps;
#else
_Thread_local uint64_t th_work_steps;
#endif

PyObject *turbohtml_work_steps(PyObject *module, PyObject *Py_UNUSED(ignored)) {
    (void)module;
    return PyLong_FromUnsignedLongLong(th_work_steps);
}
