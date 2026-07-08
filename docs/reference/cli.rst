##############
 Command line
##############

The ``turbohtml`` console script (also ``python -m turbohtml``) is a thin front end over the public API: every
subcommand reads one input and writes one output, delegating to the matching function. The reference below is generated
from the argparse parser, so it lists every subcommand and flag exactly as the command exposes them and cannot drift
from the code. For the subcommand-to-API mapping and pipeline examples, see :doc:`/how-to/cli`.

.. sphinx_argparse_cli::
    :module: turbohtml.__main__
    :func: _parser
    :prog: turbohtml
    :title: turbohtml
