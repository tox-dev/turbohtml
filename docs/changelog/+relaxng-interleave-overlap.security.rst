RELAX NG schemas whose ``interleave`` branches overlap through a wildcard name, a ``<ref>``, a ``mixed`` or a
``combine="interleave"`` now raise ``ValueError`` at compile time instead of compiling.
