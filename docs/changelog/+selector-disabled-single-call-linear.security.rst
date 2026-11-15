A single ``matches()`` or ``closest()`` call with ``:disabled``, ``:enabled``, ``:read-write`` or ``:read-only`` now
takes linear time inside disabled ``fieldset`` elements, including deeply nested ones.
