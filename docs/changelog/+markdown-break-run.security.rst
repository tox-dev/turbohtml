Converting a long run of ``<br>`` elements in :meth:`~turbohtml.Node.to_markdown` now takes linear time; a block inside
a link dropped by ``strip`` or ``convert`` ends the run.
