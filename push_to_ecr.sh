#!/bin/bash
set -e

AWS_REGION="us-east-1"
AWS_ACCOUNT_ID="${AWS_ACCOUNT_ID:?AWS_ACCOUNT_ID env var must be set}"
ECR_REPO="letterpulse"

# Parse environment flag (default: dev)
ENV="${1:-dev}"
ENV="${ENV#-}"   # Strip leading dash
ENV="${ENV#-}"   # Strip second dash (for --prod)
if [[ "$ENV" != "dev" && "$ENV" != "prod" && "$ENV" != "both" ]]; then
    echo "Usage: $0 [dev|prod|both]"
    exit 1
fi

ECR_URI="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

# Determine which environments to deploy
if [[ "$ENV" == "both" ]]; then
    ENVS=("dev" "prod")
else
    ENVS=("$ENV")
fi

echo "Deploying to: ${ENVS[*]}"

# Login to ECR
aws ecr get-login-password --region "${AWS_REGION}" \
  | docker login --username AWS --password-stdin "${ECR_URI}"

# Build image once
docker build --platform linux/amd64 -t "${ECR_REPO}:build" .

# Tag and push for each environment
for TARGET_ENV in "${ENVS[@]}"; do
    IMAGE_TAG="${TARGET_ENV}-latest"
    FULL_IMAGE="${ECR_URI}/${ECR_REPO}:${IMAGE_TAG}"

    echo "Tagging and pushing: ${IMAGE_TAG}"
    docker tag "${ECR_REPO}:build" "${FULL_IMAGE}"
    docker push "${FULL_IMAGE}"
    echo "Successfully pushed ${FULL_IMAGE}"
done