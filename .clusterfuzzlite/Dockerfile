# The image publishes only a latest tag, so the digest is the pin; Dependabot refreshes it.
FROM gcr.io/oss-fuzz-base/base-builder-python@sha256:21f6fab17ace4dc76e1c56de91ee95d3d03da021df94b41056ba036f6c6f55e9
# requirements.txt is compiled from requirements.in for the image's Python 3.11, with hashes.
COPY .clusterfuzzlite/requirements.txt /tmp/requirements.txt
RUN python3 -m pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt
# ClusterFuzzLite builds this image with the checkout under test as the context and runs compile without touching the
# source, so the image must carry the checkout; an OSS-Fuzz project Dockerfile clones the repository here instead
COPY . $SRC/turbohtml
COPY .clusterfuzzlite/build.sh $SRC/
WORKDIR $SRC/turbohtml
