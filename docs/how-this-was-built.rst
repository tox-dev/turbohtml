#########################
 How turbohtml was built
#########################

turbohtml 1.0 was not written by hand. It was built over about a month of continuous background co-work with Anthropic's
Claude Opus 4.8, with some help from Fable, across close to 300 pull requests and commits and many rounds of iteration.

I reviewed most of the code myself, and I put real work into the guardrails around it. turbohtml is differentially
tested against a wide range of existing libraries across the Python, Rust, C, C++, and Go ecosystems, exercised through
their own test suites. The aim was correctness as much as speed, checked against the tools that came before.

None of this would exist without that prior work. turbohtml stands on the shoulders of the competing libraries across
those ecosystems and on the specifications for HTML, CSS, and JavaScript and the people who wrote them. The saying about
standing on the shoulders of giants applies here in full. My thanks to all of them.

Some engines owe a specific debt. The html5lib-tests conformance suite and lexbor shaped the parser and tree builder;
the encoding and language detectors follow the models chardetng and whatlang published. Where I reused another project's
code or data, its license requires attribution, which lives in the ``NOTICE`` file at the repository root.
