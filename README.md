# Morrow UI Lab

A detached, mock-backed frontend laboratory based on the current experimental Morrow B UI.

**This is not the running B.** Nothing here is connected to the experimental/production databases, provider sessions, Codex auth, Supabase, or VPS services.

## Run

```bash
npm install
npm run dev
```

Open the Vite preview URL. `mock-api.js` auto-authenticates and provides fake data.

## Give it to any AI

Give the AI `AI_HANDOFF.md` first. Tell it to work in this repository only and keep the work frontend-only.

Suitable for Google AI Studio, Gemini coding tools, Claude, Codex, Muse, or another coding agent that can edit a web project.

## Merge rule

Do not copy the lab wholesale into the running B. Once a design is accepted, compare its frontend diff with B and port only the selected layout/styles while preserving real B API contracts and runtime behavior.

See `SOURCE_BASE.md` for the exact source commit.
