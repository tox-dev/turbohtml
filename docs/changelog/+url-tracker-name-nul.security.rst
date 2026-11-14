``normalize_url``, ``clean_url`` and ``extract_links`` no longer crash on a query or fragment key holding an encoded
NUL, which is kept unless another rule drops it.
