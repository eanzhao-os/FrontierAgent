# FrontierAgent (fork) — project notes

## Frontend design system of record

All frontend work in `apodex/web_static/index.html` (single-file React via CDN, no build step)
MUST follow the design system at `~/Code/native-ai-ui` — specifically the framework-agnostic
skill `~/Code/native-ai-ui/skills/native-ai-ui/`:

- `references/tokens.md` — verbatim light/dark token blocks (CSS variables); reuse these
  variable names and values; do not invent new colors or shadows ad hoc.
- `references/design-principles.md` — layout, spacing, typography, and interaction rules.
- `assets/preview/native-chat.html` — the visual reference mockup.

Rules:

1. Reference that skill BEFORE any UI change; lift tokens and patterns from it rather than
   improvising. The .tsx components in native-ai-ui are NOT the reference (no build step here).
2. Keep the single-file, no-build architecture: no Vite/Next/webpack, no new frontend deps.
3. Before large edits to `index.html`, check file freshness (`wc -l`, `git diff`) — concurrent
   sessions have rewritten this file before and produced a merged-corrupt result.
4. After ANY edit to `index.html`, parse the whole `<script type="text/babel">` region with a
   real JSX parser (e.g. `@babel/parser` with the jsx plugin). A syntax error anywhere in it
   blanks the entire page; `node --check` only covers the plain-JS FA_PURE region.

## Untouched user files

`plugins/tools/bazi_query.py`, `plugins/tools/__init__.py`, and
`workflows/stateful_react_agent/__init__.py` carry the user's own uncommitted work — never
commit, revert, or "clean up" them.
