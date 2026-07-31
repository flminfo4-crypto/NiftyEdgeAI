# Wireframes

For NiftyEdgeAI, the wireframes are not static mockup images — they're a working HTML/CSS/JS prototype, which is more useful to build against than flat images since spacing, states, and interactions are all real.

**See `../../frontend/`** — open `frontend/index.html` in a browser (or any other page listed in `frontend/README.md`) to view the full set of screens.

## Why HTML instead of Figma/PNG wireframes here

- Every screen is pixel-accurate and interactive (theme switching, tabs, toggles all work) rather than a flat comp.
- The markup and CSS class structure (`frontend/css/style.css`) can be referenced directly when building the real frontend, rather than re-deriving spacing/tokens from an image.
- Three themes (Light / Dark / Terminal) are all live in the same files — no separate wireframe set per theme needed.

## Screen inventory

See the table in `frontend/README.md` for the full list of 15 pages. At a glance, the flows covered are:

1. **Pre-market** — CPR Dashboard
2. **Market analysis** — Dashboard, Market Profile, Volume Profile, Footprint, Options Chain, Open Interest, Greeks
3. **Trading** — Strategy Signals, Positions, Orders
4. **Research / ops** — Backtester, Reports
5. **Account** — Settings, Help

## If flat images are still needed

For stakeholder decks or a design tool handoff, screenshot each `frontend/*.html` page (in all three themes if relevant) rather than redrawing them — the HTML is the source of truth.
