#!/bin/bash
set -e

AWS_REGION="us-east-1"
AWS_ACCOUNT_ID="093239908030"
ECR_REPO="letterpulse"

# Parse environment flag (default: dev)
ENV="${1:-dev}"
ENV="${ENV#-}"   # Strip leading dash
ENV="${ENV#-}"   # Strip second dash (for --prod)
if [[ "$ENV" != "dev" && "$ENV" != "prod" ]]; then
    echo "Usage: $0 [dev|prod]"
    exit 1
fi
IMAGE_TAG="${ENV}-latest"

ECR_URI="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
FULL_IMAGE="${ECR_URI}/${ECR_REPO}:${IMAGE_TAG}"

echo "Deploying to: $ENV ($IMAGE_TAG)"

# Login to ECR
aws ecr get-login-password --region "${AWS_REGION}" \
  | docker login --username AWS --password-stdin "${ECR_URI}"

# Build image
docker build --platform linux/amd64 -t "${ECR_REPO}:${IMAGE_TAG}" .

# Tag for ECR
docker tag "${ECR_REPO}:${IMAGE_TAG}" "${FULL_IMAGE}"

# Push
docker push "${FULL_IMAGE}"

echo "Successfully pushed ${FULL_IMAGE}"