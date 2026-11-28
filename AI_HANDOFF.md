# Morrow UI Lab — AI handoff

## Job

Redesign and reorganize the **frontend only** of an AI companion app. This detached UI lab is based on the current Morrow B frontend. Reuse existing interaction patterns where useful; do not redesign the backend or invent a new memory architecture.

## Target primary navigation

Use five stable top-level destinations:

1. **Home** — real life dashboard: today/time, companion state, proactive notes, anniversaries and small life cards.
2. **Memory** — Recent, long-term Memory, Episode/recall views, plus an “About us” entry for stable user/assistant/relationship information.
3. **Chat** — clean daily conversation. Keep a chat-side drawer for quick chat controls and shortcuts.
4. **More** — the safe holding area for useful life features that do not yet deserve a primary tab: Journal, Album, Calls, Notes, Keepsake, Voice Letter, Tide/cycle, Alarm, future social/feed ideas, etc.
5. **Settings** — provider/model connection, TTS, MCP, Prompt, account/data/backup, usage, and developer/debug settings.

## Product decisions

- **More must exist.** If ownership is unclear, put a feature in More first instead of polluting primary navigation.
- Current Morrow side navigation may be kept, but should become a **Chat quick drawer**, not global navigation.
- `Home` must become a real home/life page. Current Morrow “Home” is actually Chat and should not stay semantically confused.
- `Prompt` ultimately belongs under Settings / Companion, not as ordinary Memory.
- Stable profile/relationship information can surface from Memory → About us.
- Developer-only items such as HTTP logs belong under Settings → Developer.
- Do not delete useful features because their final home is uncertain. Re-home them to More or Developer first.

## Current Morrow navigation to re-home

- Home → actually Chat.
- Notes → likely More.
- Memory → keep, but legacy Forever/Fragment concepts are retired; future Memory/Recall will replace them.
- Kept → journal-like; likely More → Journal.
- Tide → cycle/life record; More.
- Room / Footprint → inspect; rename to a real life feature or park in More.
- Cabinet / Tool → Settings → Developer or More → Tools, depending user value.
- Cabinet / Abstract → continuity/debug; normally Settings → Developer.
- Cabinet / Together → inspect; life-facing content can go Home/More.
- Drawer / Voice → More → Voice Letter.
- Drawer / Alarm → More → Alarm.
- Drawer / Keepsake → More → Keepsake / Album depending final semantics.
- Settings → keep, grouped by Companion / Providers / Voice / MCP / Data / Developer.

## Hard boundaries

- Frontend / UI / UX / information architecture only.
- Do **not** add or redesign a production backend.
- Do **not** create a second memory system.
- Do **not** change production API contracts as part of visual redesign.
- Do **not** add real credentials or connect to a real database.
- Do **not** remove existing features merely to make the UI cleaner; re-home or hide behind More/Developer.
- Mobile first: iPhone and iPad are primary. Respect safe areas, `100dvh`, keyboard behavior, touch targets and PWA use.
- Chat should feel spacious. Avoid permanently wasting chat vertical space with a large bottom bar if a better chat-specific treatment exists.
- Preserve an easy return to Home.

## Mock environment

`mock-api.js` intercepts `/api/*` calls so the frontend can be previewed without the real B backend. Extend mock responses only when needed for visual preview; do not turn the mock layer into app architecture.

## Expected deliverable

- One coherent mobile-first shell with Home / Memory / Chat / More / Settings.
- Existing Morrow features redistributed into understandable places.
- Chat drawer preserved for quick chat controls / life shortcuts.
- Navigation works in mock preview.
- No backend dependencies added.
- Update `UI_CHANGELOG.md` with what moved where and what remains parked in More/Developer.
