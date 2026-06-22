##########
 Sanitize
##########

.. module:: turbohtml.sanitizer

Sanitize untrusted HTML against an allowlist, a successor to ``bleach.clean``. Build a :class:`Policy` (or take a
preset), then sanitize. A non-overridable baseline removes scripting elements, event-handler attributes, and
``javascript:`` URLs regardless of the policy.

.. autofunction:: sanitize

.. autoclass:: Sanitizer
    :members: sanitize

.. autoclass:: Policy
    :members: strict, basic, relaxed

.. autoclass:: OnDisallowed
    :members:

****************************
 turbohtml.migration.bleach
****************************

.. module:: turbohtml.migration.bleach

A drop-in for ``bleach.clean`` for projects migrating off bleach. It translates bleach's arguments onto a
:class:`~turbohtml.sanitizer.Policy`; the safety baseline still applies, so an ``attributes`` callable cannot re-admit
an event handler or a ``javascript:`` URL.

.. autofunction:: clean
