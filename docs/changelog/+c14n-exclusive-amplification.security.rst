:meth:`turbohtml.Node.canonicalize` now raises ``ValueError`` when repeated namespace declarations outgrow the rest of
the output, so a small document can no longer expand into gigabytes.
