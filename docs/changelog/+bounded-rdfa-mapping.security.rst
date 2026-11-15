RDFa ``vocab`` and ``prefix`` IRIs longer than 256 code points are now ignored, which keeps ``rdfa()`` memory bounded on
untrusted pages.
