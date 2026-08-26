#!/bin/sh
set -eu

# Keep FastEmbed's cache root writable for lock/metadata files while reusing
# the packaged model tree in-place. This avoids copying ~90 MB into /tmp on
# every container start and still supports arbitrary-UID/read-only-root runs.
mkdir -p /tmp/fastembed
model_cache="models--qdrant--all-MiniLM-L6-v2-onnx"
if [ ! -e "/tmp/fastembed/${model_cache}" ]; then
    ln -s "/opt/models/${model_cache}" "/tmp/fastembed/${model_cache}"
fi

exec "$@"
