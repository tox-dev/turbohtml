RELAX NG schemas whose ``interleave`` branches overlap through a wildcard name, a ``<ref>``, a ``mixed`` or a later
``combine="interleave"`` part now raise ``ValueError`` at compile time instead of compiling. Interleave checks on very
large grammars raise ``ValueError`` once they exceed a fixed amount of work.
