``minify()`` writes text and attribute values in the shortest form that reparses to the same tree. ``&`` stays literal
where it cannot start a reference, ``>`` and U+00A0 stay literal in text, an attribute value takes whichever quote it
holds fewer of, and a value ending in ``/`` or holding a literal ``&`` drops its quotes. Pages come out 0.3-1.3% smaller
in 0.6-2.8% fewer instructions.
