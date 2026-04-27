# Market AI — Frontend

React 18 + TypeScript + Vite + Tailwind SPA for the Market AI trading dashboard.

## Prerequisites

- Node.js 18+
- pnpm (`npm i -g pnpm`)
- FastAPI backend running on port 8000

## Development

```bash
cd frontend
pnpm install
pnpm dev
```

Opens at `http://localhost:5173/` in dev. API calls proxy to `http://localhost:8000`.

## Build (production)

```bash
pnpm build
```

Outputs to `../ui/static/dist/`. FastAPI then serves the SPA from root browser routes.

## Tests

```bash
pnpm test
```

Runs vitest smoke tests for each of the 9 pages.

## Serving from FastAPI

```bash
uvicorn api.main:app --reload
```

Navigate to `http://localhost:8000/overview`.

## Stack

- **React 18** + React Router 6
- **TanStack Query 5** — data fetching, 15s refetch interval
- **Zustand 4** — bot state store, updated from control POST responses
- **Recharts 2** — equity curve, drawdown, sentiment trend charts
- **Tailwind CSS 3** + CSS custom properties for design tokens
- **lucide-react** — icons
- **Zod** — API payload validation
- **@fontsource/inter** + **@fontsource/jetbrains-mono** — self-hosted fonts

## Personalisation

A floating Tweaks panel (bottom-right) lets you change:
- Accent colour hue (cyan default: 198)
- Layout density (dense / balanced / airy)

Settings persist to `localStorage` under `mai_tweaks`.
