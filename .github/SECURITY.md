# Security Policy

turbohtml parses untrusted bytes in a C extension and sanitizes untrusted markup, so its security surface is real. This
policy says what we treat as a vulnerability, how to report one, and what we promise back.

## Supported versions

| Version        | Security fixes                    |
| -------------- | --------------------------------- |
| latest release | yes                               |
| older releases | critical issues only, best effort |

Fixes land on the latest release line. We patch an older line only for a critical issue, and only when the fix ports
cleanly.

## Reporting a vulnerability

Report privately through GitHub's [Report a vulnerability](https://github.com/tox-dev/turbohtml/security/advisories/new)
flow. Do not open a public issue for a suspected vulnerability.

Include:

- the affected version (`turbohtml.__version__`),
- a minimal input that reproduces the problem,
- the configuration you used (default, or the exact non-default options),
- the impact you observed or expect.

A reproducer we can run is worth more than a description. If you cannot reach GitHub, email the maintainer listed on the
[PyPI project page](https://pypi.org/project/turbohtml/).

## What counts as a security issue

turbohtml both parses bytes and sanitizes markup, so we classify by whether an attacker who controls the input can reach
active content or corrupt memory.

In scope:

- **Sanitizer bypass under the default configuration.** Sanitized output still executes or can execute script. A
  surviving `<script>`, an `on*` handler, a `javascript:` or `data:` URL that reaches an attribute, mutation-XSS where
  the serialized output reparses into an executable tree, and namespace confusion across an SVG or MathML integration
  point all qualify. This is the highest priority.
- **Memory-safety fault in the C extension on untrusted input.** An out-of-bounds read or write, a use-after-free, a
  type confusion, or an integer or buffer overflow reachable by parsing, serializing, or sanitizing untrusted bytes. A
  crash counts even without a proven exploit; corrupting memory is enough.
- **A parser or spec divergence that enables a sanitizer bypass.** A tree-construction quirk is a security issue when it
  moves an element across a namespace boundary the sanitizer relies on, or when it hides an executable construct from
  the allowlist walk. The bypass it enables is the harm, not the divergence itself.
- **Unbounded resource use from a small input.** Algorithmic-complexity or expansion blowup that turns a short input
  into a stack overflow, a hang, or a runaway allocation.

Out of scope (report these as a normal [issue](https://github.com/tox-dev/turbohtml/issues)):

- **A spec divergence with no security impact.** Output that differs from the WHATWG algorithm but stays inert, for
  example a mis-nested `<table>` or an attribute serialized in a different but safe form. These are correctness bugs; we
  want them, through the normal issue tracker.
- **A bypass that needs a non-default, deliberately unsafe configuration.** If reaching active content requires a policy
  the caller wrote to allow it, that is a hardening or documentation matter, not a vulnerability in the default.
- **A clean exception on malformed input.** A raised Python error that the caller can catch is a bug report, not a
  memory-safety issue.
- **A finding that reproduces only under a debug or instrumented build,** or only from trusted, local input the caller
  already controls.

The two calls we make explicitly: a sanitizer bypass is a vulnerability only against the default configuration, and a
parser crash is a vulnerability when it corrupts memory or drives a denial of service, not when it is a catchable
exception or an inert divergence.

## What to expect

turbohtml is volunteer-maintained, so these are reasonable-effort targets, not a contractual SLA:

- We acknowledge a report within five business days.
- We aim to ship a fix or a documented mitigation within 90 days, and we coordinate the disclosure date with you.
- For an accepted vulnerability we publish a GitHub Security Advisory and request a CVE through GitHub, which is a CVE
  Numbering Authority.
- We credit reporters by the name they choose, and name a reporter only with permission.

## Safe harbor

We will not pursue good-faith research conducted under this policy. Touch only data you own, avoid disrupting others,
and give us reasonable time to remediate before you disclose publicly.

## Supply-chain provenance of generated tables

Some `_c/data/` tables are generated from a pinned upstream source rather than written by hand. `tools/generate_tlds.py`
fetches the IANA top-level-domain list from `data.iana.org`, and `tools/generate_psl.py` fetches the Public Suffix List
from GitHub. A poisoned or silently changed upstream table is a vulnerability that no review of the `.c` sources would
catch, the class the xz-utils backdoor (CVE-2024-3094) exploited by hiding in a generated build artifact.

To make a regeneration verifiable, each generator pins the SHA-256 of the exact upstream bytes the committed table was
built from, on top of the version or commit it already names: `generate_tlds.py` pins `IANA_VERSION` and `IANA_SHA256`,
`generate_psl.py` pins `PSL_COMMIT` and `PSL_SHA256`. A rebuild recomputes the digest of the download and aborts when it
does not match the pin, so a changed or poisoned source cannot land silently. A real upstream bump is deliberate: a
maintainer reviews the new source, updates the version-or-commit pin and its checksum together, and regenerates, so both
the changed pin and the table diff show up in code review.
