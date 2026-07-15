# Anchor — web application

The Next.js front end for Anchor. The Agent API (FastAPI) is the engine; this
is the product surface.

Full architecture, deployment and demo notes live in the
[repository README](../README.md).

## Run it

```bash
npm install
cp .env.example .env.local     # point ANCHOR_API_URL at your backend
npm run dev                    # http://localhost:3000
```

The backend must be running too. With Docker:

```bash
cd .. && docker compose up --build -d
docker exec anchor-ollama ollama pull llama3.2:3b
```

## Scripts

| Command | What it does |
|---|---|
| `npm run dev` | Development server with hot reload |
| `npm run build` | Production build (also the type check) |
| `npm start` | Serve the production build |
| `npx tsc --noEmit` | Type check only |
| `npx eslint app components lib hooks types` | Lint |

## Environment

| Variable | Scope | Purpose |
|---|---|---|
| `ANCHOR_API_URL` | Server only | FastAPI base URL, read by the route handlers |
| `NEXT_PUBLIC_API_URL` | Public | Optional; only builds the "API Docs" link |

`ANCHOR_API_URL` is never exposed to the browser. The access token lives in an
httpOnly cookie and is attached to upstream requests by the route handlers, so
the client bundle contains no credential and production needs no CORS grant on
the API.

## Layout

```
app/
  page.tsx              public landing page
  login/                sign-in
  (app)/                behind the session gate
    assistant/          the primary screen
    knowledge/          documents + ingestion
    activity/           recent requests
    analytics/          metrics
    settings/           safe runtime configuration
  api/                  route handlers: auth + backend proxy
components/             ui, layout, landing, assistant, knowledge, analytics
hooks/                  useAuth, useChat, useAsync
lib/                    api-client (the only fetch), backend, session, format
types/api.ts            typed backend contracts
```
