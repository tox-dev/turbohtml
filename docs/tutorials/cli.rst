####################################
 Doing a one-off job from the shell
####################################

Not everything deserves a script. To convert one file, sniff one encoding, or shrink one page, the ``turbohtml`` console
script puts the same surface on the command line. Each subcommand reads stdin (or a file argument) and writes stdout.

Turn a fragment into Markdown:

.. code-block:: console

    $ echo '<h1>Title</h1><p>Body</p>' | turbohtml to-markdown
    # Title

    Body

Sniff the encoding of a downloaded page, printing just the name:

.. code-block:: console

    $ curl -s https://example.com | turbohtml detect
    UTF-8

Minify a page in place, folding its inline CSS too, and write the result to a new file:

.. code-block:: console

    $ turbohtml minify --minify-css page.html -o page.min.html

The other subcommands cover ``minify-css``, ``minify-js``, ``to-text``, and ``sanitize`` against the default policy. A
subcommand exits ``0`` on success, ``1`` when the library rejects the input, and ``2`` on a bad argument, so it composes
in a shell pipeline. For policies, renderer options, or streaming, call the API from Python.

That closes the tour: the same parse, query, clean, and convert surface, reached from a prompt. The :doc:`/how-to/cli`
guide and the generated :doc:`/reference/cli` list every subcommand and flag.
