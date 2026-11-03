# The image publishes only a latest tag, so the digest is the pin; Dependabot refreshes it.
FROM gcr.io/oss-fuzz-base/base-builder-python@sha256:21f6fab17ace4dc76e1c56de91ee95d3d03da021df94b41056ba036f6c6f55e9
# The build tools come hash-pinned from the fuzz-build group of the release build's lock.
COPY --from=ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 /uv /bin/
COPY pylock.pgo.toml /tmp/pylock.pgo.toml
RUN uv pip install --no-cache --preview-features pylock --require-hashes --system --python python3 \
    --requirements /tmp/pylock.pgo.toml --group fuzz-build
# ClusterFuzzLite builds this image with the checkout under test as the context and runs compile without touching the
# source, so the image must carry the checkout; an OSS-Fuzz project Dockerfile clones the repository here instead
COPY . $SRC/turbohtml
COPY .clusterfuzzlite/build.sh $SRC/
WORKDIR $SRC/turbohtml
