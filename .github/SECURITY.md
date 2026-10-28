# Security policy

turbohtml is a Python HTML toolkit whose C extension tokenizes, parses, queries, serializes and sanitizes untrusted
markup. This policy says how to report a security problem and how we decide whether a report is one.

## Reporting a vulnerability

**Do not open a public issue for a security problem.** Use
[GitHub's private vulnerability reporting](https://github.com/tox-dev/turbohtml/security/advisories/new). If neither
works for you, email [gaborjbernat@gmail.com](mailto:gaborjbernat@gmail.com).

Include what the attacker controls and what they gain, the steps or a proof of concept, and the versions and platform
you tested. Write the report yourself; unreviewed tool or model output takes longer to triage.

## What to expect

Volunteers maintain turbohtml, with no on-call rotation. We aim to acknowledge a report within **5 working days** and
assess it within **15 working days**. Fixes are developed in a private fork attached to the draft advisory, ship in a
new release, and the advisory, with a CVE requested through GitHub, goes out with that release. The default embargo is
**90 days** from the report; we agree any change with the reporter. We credit reporters in the advisory under the name
they choose, or not at all. There is no bug bounty.

## Supported versions

The latest release receives fixes. An older line gets a fix only for a critical issue, best effort, and only when the
fix ports cleanly.

## Vulnerability or bug

A report is a vulnerability when an attacker who controls only an input that the [threat model](THREAT_MODEL.md) lists
as untrusted breaks a guarantee turbohtml makes. A report that first needs control of something the threat model trusts,
such as a sanitizer `Policy` or options the caller wrote, is a bug: we fix it in the open and publish no advisory.
Classify by who supplies the dangerous input, not by how bad the outcome sounds.

**In scope**

- A sanitizer or `linkify` bypass under the default `Policy()`: sanitized output that runs script once a browser parses
  it, including mutation XSS and namespace confusion across SVG and MathML.
- A memory-safety fault in the C extension reachable from untrusted input through the public API: out-of-bounds read or
  write, use-after-free, type confusion, integer overflow. A crash counts without a proven exploit.
- A tree-construction divergence that moves an element across a namespace boundary or hides it from the sanitizer's
  allowlist walk.
- A small input that drives a hang, stack overflow or runaway allocation, and, rated Low, super-linear time or memory
  that matters only for inputs far larger than an attack needs.
- An XSLT `xsl:import` that reads a file outside `import_root`.

**Out of scope**

- A bypass that needs a non-default `Policy` the caller wrote to allow the dangerous element, attribute or URL scheme;
  that is a hardening matter.
- A spec divergence that stays inert, such as a mis-nested `<table>`.
- A Python exception on malformed input that the caller can catch.
- A finding that reproduces only in a sanitizer or `-Dfuzzing=true` build, for example by reaching its operation limit,
  or only from input the caller controls.
- `xsl:import` reading local files when the caller kept `allow_imports=True` without an `import_root`; set
  `allow_imports=False` for untrusted stylesheets.
- `escape` and `unescape` matching `html.escape` and `html.unescape`, and `Markup` from `migration.markupsafe` treating
  its argument as safe markup; both are their contracts.
- The 512-level tree depth cap flattening deeper nesting.
- `tools/`, `docs/`, `tests/` and the vendored test suites and corpora.

If you cannot tell which side a finding falls on, report it privately and we will decide.

## Safe harbor

We will not pursue good-faith research conducted under this policy. Touch only data you own, avoid disrupting others,
and give us reasonable time to fix the problem before you disclose it.
