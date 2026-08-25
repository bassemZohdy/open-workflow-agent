#!/bin/sh
set -eu

# The model is packaged in the image for offline startup, while FastEmbed
# writes small index/lock metadata during its first use. Stage that metadata
# into the writable tmpfs so arbitrary-UID, read-only-root containers work.
mkdir -p /tmp/fastembed
if [ ! -e /tmp/fastembed/models--qdrant--all-MiniLM-L6-v2-onnx ]; then
    cp -a /opt/models/. /tmp/fastembed/
fi

exec "$@"
