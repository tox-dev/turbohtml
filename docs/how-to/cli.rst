############################
 Run turbohtml from a shell
############################

Drive turbohtml from a shell. The ``turbohtml`` console script exposes the parse, query, and convert surface over stdin
and stdout, for one-off jobs and pipelines.

***************************
 The ``turbohtml`` command
***************************

``python -m turbohtml`` (installed as the ``turbohtml`` console script) is a thin front end over the public API, for the
same one-off jobs ``html2text``, ``markdownify``, ``htmlmin``, ``inscriptis``, ``minify-html``, ``charset-normalizer``,
and ``courlan`` expose on the command line. Each subcommand reads one input (a file argument, or stdin when the argument
is omitted or ``-``) and writes to stdout, or to the ``-o FILE`` given:

.. list-table::
    :header-rows: 1
    :widths: 22 78

    - - Subcommand
      - Calls
    - - ``minify``
      - :func:`turbohtml.clean.minify`; ``--minify-css`` also minifies ``<style>`` bodies and ``style`` attributes.
    - - ``minify-css``
      - :func:`turbohtml.clean.minify_css`
    - - ``minify-js``
      - :func:`turbohtml.clean.minify_js`
    - - ``detect``
      - :func:`turbohtml.detect.detect`, printing the encoding name of the input bytes.
    - - ``to-markdown``
      - :meth:`turbohtml.Node.to_markdown` on the parsed input.
    - - ``to-text``
      - :meth:`turbohtml.Node.to_text` on the parsed input.
    - - ``sanitize``
      - :func:`turbohtml.clean.sanitize` against the default policy.

.. code-block:: console

    $ echo '<h1>Title</h1><p>Body</p>' | python -m turbohtml to-markdown
    # Title

    Body

    $ python -m turbohtml minify --minify-css page.html -o page.min.html

    $ curl -s https://example.com | python -m turbohtml detect
    UTF-8

A subcommand exits ``0`` on success and ``1`` with a message on stderr when the library rejects the input (an unparsable
script, an empty byte stream to ``detect``) or the input file cannot be read; argument errors exit ``2``. For policies,
renderer options, or streaming, call the API from Python.
