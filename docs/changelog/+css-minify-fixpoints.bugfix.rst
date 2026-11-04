Keep CSS minification stable across repeated passes when an unquoted `@import url(` holds a quote, a comment is left
open at the end of the input, merged rules match the rule before them, or stray top-level text runs into the next node;
`StyleRule.selector_text` also keeps a trailing escaped space.
