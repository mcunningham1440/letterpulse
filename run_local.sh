#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
IMAGE_NAME="letterpulse_local"
CONTAINER_NAME="letterpulse_local"

# Stop and remove existing container (if running)
docker rm -f "$CONTAINER_NAME" 2>/dev/null || true

# Build the image
docker build --platform linux/arm64 -t "$IMAGE_NAME" "$SCRIPT_DIR"

# Run the container
docker run \
    --name "$CONTAINER_NAME" \
    -p 8000:8000 \
    --env-file "$SCRIPT_DIR/.env" \
    "$IMAGE_NAME"