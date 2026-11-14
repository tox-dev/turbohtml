Stop ``select()``, ``matches()``, ``closest()`` and ``computed_style()`` crashing on a detached element when a selector
looks at its parent through ``>``, ``:root``, ``:scope`` or an ``option``'s ``:disabled``/``:enabled``.
