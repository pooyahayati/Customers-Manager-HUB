# Web Runtime Skeleton — Execution Scope

**Branch:** `feat/web-runtime-skeleton`  
**Parent specification:** `specs/MILESTONE-1-RUNTIME-SKELETON.md`

This document defines only the frontend implementation slice of Milestone 1. The parent specification, approved technical baseline, and ADR-007 remain authoritative.

## Goal

Create the smallest production-oriented Next.js runtime that proves the web application can install reproducibly, type-check, lint, build, run in Docker, and respond over HTTP.

`apps/web` is the runtime foundation for the **Admin Console**. It is not the customer-facing Website Chat/Webchat client. The customer Webchat will be a separate lightweight frontend artifact created only when the Website Channel milestone begins, as defined by ADR-007.

No product UI or business behavior is part of this slice.

## Runtime and package management

Use the approved baseline:

- Node.js 24 LTS.
- pnpm 12.x; pin the root `packageManager` field to the approved bootstrap tool version.
- Next.js 16.3.x.
- TypeScript with `strict: true`.
- React versions required by the selected Next.js release.

The committed `pnpm-lock.yaml` is the dependency authority.

Do not create npm or Yarn lockfiles.

## Files to create

Create only files required for a runnable workspace and application:

```text
package.json
pnpm-workspace.yaml
pnpm-lock.yaml
apps/web/package.json
apps/web/next.config.ts
apps/web/tsconfig.json
apps/web/app/layout.tsx
apps/web/app/page.tsx
apps/web/app/globals.css
apps/web/Dockerfile
```

Additional generated Next.js type/config files may be committed only if the selected Next.js setup genuinely requires them.

Do not create shared packages, Webchat packages, or empty future directories.

## Root workspace

The root pnpm workspace initially contains only `apps/web`.

Root scripts should provide only useful workspace-level commands needed now, such as:

- `lint`
- `typecheck`
- `build`

Do not add a monorepo orchestration framework such as Turborepo or Nx at this stage.

## Web application

Use the Next.js App Router.

This application is the future Admin Console surface, but the initial page must remain intentionally minimal and may contain only a simple bootstrap message such as:

```text
Customers Manager HUB
System bootstrap running
```

No dashboard mockups, navigation systems, authentication screens, charts, forms, fake data, or product placeholders.

Use plain CSS only for the minimal bootstrap page. Do not add a UI framework or CSS framework.

Do not implement or embed the customer-facing Webchat in this application.

## Dependency policy

Production dependencies should initially be limited to the dependencies required by Next.js itself:

- `next`
- `react`
- `react-dom`

Development dependencies should be limited to the TypeScript and lint tooling genuinely required by the chosen Next.js setup.

Do not add:

- Tailwind CSS.
- shadcn/ui.
- Material UI, Chakra UI, Ant Design, or another component framework.
- Redux, Zustand, MobX, or another state-management library.
- TanStack Query or another data-fetching state layer.
- Axios or another HTTP client when the platform APIs already suffice.
- Form libraries.
- Testing frameworks before there is frontend behavior worth testing.
- Prettier unless a clear formatting gap remains after the selected lint/tooling setup.

Every added dependency must have an immediate runtime or quality-gate purpose.

## TypeScript and linting

TypeScript strict mode is mandatory.

Use one authoritative lint path. Keep the ESLint configuration minimal and aligned with the selected Next.js release if ESLint is used.

Do not introduce overlapping lint or formatting stacks.

## Docker

`apps/web/Dockerfile` must:

- Use an explicit Node 24 image tag; never `latest`.
- Use pnpm through a pinned standard mechanism.
- Install with the committed lockfile in frozen mode.
- Use a multi-stage production build where it materially reduces the runtime image.
- Run as a non-root user where practical.
- Expose port `3000`.
- Avoid copying development-only dependencies into the final runtime image where practical.
- Never bake secrets into the image.

Prefer Next.js standalone output if it provides a simpler and smaller production runtime without additional framework complexity.

## Compose integration

Only after the web image builds and runs successfully, add a `web` service to `compose.yaml`.

Requirements:

- Build from `apps/web/Dockerfile`.
- Configurable host port with default `3000`.
- HTTP health check if practical with the existing runtime image.
- Do not depend on `api` unless the bootstrap page actually requires API availability. For this slice it should not.

Add `WEB_PORT` to `.env.example` only if Compose uses it.

## Makefile

Extend the root `Makefile` only if doing so gives a useful project-wide command for the now-existing frontend checks.

Do not add wrappers that merely duplicate rarely used commands.

## Explicitly out of scope

This frontend slice must not implement:

- Authentication or RBAC UI.
- Tenant management.
- Admin dashboard.
- Website chat widget or customer Webchat runtime.
- API client/domain SDK.
- AI/agent configuration screens.
- Contacts, conversations, analytics, knowledge, tools, handoff, or settings pages.
- Internationalization.
- Theme systems.
- Component libraries/design systems.
- Browser state architecture.
- Frontend test framework without meaningful frontend behavior to test.

## Acceptance gate

Before this slice is ready for review, all applicable checks must pass against committed lockfiles:

```text
pnpm install --frozen-lockfile
pnpm lint
pnpm typecheck
pnpm build
docker compose -f compose.yaml config --quiet
docker compose -f compose.yaml build web
docker compose -f compose.yaml up -d web
```

The running web service must respond successfully on its configured port and must not enter a restart loop.

Repository hygiene requirements:

- `pnpm-lock.yaml` committed.
- `node_modules/` and `.next/` not committed.
- No real secrets.
- No unnecessary application packages or future-feature directories.
