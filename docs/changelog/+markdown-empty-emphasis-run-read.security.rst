Fix an out-of-bounds read in ``to_markdown`` for an emphasis element wrapping an image without alt text in
``Markdown.Images(mode="alt")``; such an image now adds nothing to the output.
