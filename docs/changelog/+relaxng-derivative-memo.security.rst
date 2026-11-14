:class:`RELAX NG <turbohtml.validate.RelaxNG>` validation now derives a shared definition once per event, so a schema
that reuses a definition along several paths, or lets one element take many overlapping attributes, validates in time
that grows with the schema instead of exponentially.
