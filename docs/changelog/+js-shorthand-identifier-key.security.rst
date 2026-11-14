Reject an object shorthand property whose key is not an identifier, such as ``{[a]}``, ``{[a] = 1}`` or ``{"a"}``, when
minifying JavaScript. Nesting such objects in each other's computed keys no longer takes time exponential in the nesting
depth.
