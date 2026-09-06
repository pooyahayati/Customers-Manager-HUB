# ADR-006: Defer Self-Hosted Object Storage Backend Selection

**Status:** Accepted  
**Date:** 2026-09-06

## Context

The product requires object storage for uploaded knowledge files, voice/audio, images, documents, and future exports.

Earlier bootstrap documentation named MinIO as the default self-hosted S3-compatible backend. That assumption is no longer suitable for the initial commercial baseline because the previously relied-on Community prebuilt distribution path is not an appropriate maintained dependency for this project.

The application architecture does not require a specific object-storage vendor; it requires an S3-compatible storage contract.

## Decision

- Keep the application storage boundary S3-compatible.
- Do not include a self-hosted object-storage service in the current bootstrap `compose.yaml`.
- Do not treat MinIO as the project default.
- Defer selection of the self-hosted backend until the file-storage/knowledge milestone, when maintained options can be evaluated against security, licensing, operational simplicity, backup/restore, and S3 compatibility.
- Cloud/managed S3-compatible providers remain valid deployment options.
- Environment variables for S3 configuration may exist as inactive placeholders, but no backend-specific credentials or defaults belong in bootstrap configuration.

## Consequences

- Milestone 1 does not need object storage running locally.
- PostgreSQL + pgvector and Redis are the only bootstrap infrastructure services currently required.
- File/media features must not be implemented until a storage backend is selected or an external S3-compatible provider is explicitly configured.
- Existing references that describe MinIO as a definitive default are superseded by this ADR.

## Supersedes

This ADR supersedes earlier draft documentation that names MinIO as the default self-hosted object-storage implementation, including that statement in `ARCHITECTURE.md` and earlier bootstrap notes.
