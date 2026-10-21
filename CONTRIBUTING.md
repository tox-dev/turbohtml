# Contributing to turbohtml

```console
git clone https://github.com/tox-dev/turbohtml
cd turbohtml
git submodule update --init tests/html5lib-tests   # conformance data for the test suite
uvx --with tox-uv tox r -e 3.15   # build, test, and check coverage
```

The [Development docs](https://turbohtml.readthedocs.io/en/latest/development/) cover everything else: the project
layout, the architecture, the pull-request checklist, the coverage gates, and the maintainer tasks (regenerating tables,
releasing).

Add a `docs/changelog/+short-name.<category>.rst` fragment for each user-facing change. The
[development guide](https://turbohtml.readthedocs.io/en/latest/development/#news-fragments) lists the categories and
shows an example. A maintenance-only pull request can use the `skip news` label.

Everyone interacting with the project is expected to be open, considerate, and respectful of others.
