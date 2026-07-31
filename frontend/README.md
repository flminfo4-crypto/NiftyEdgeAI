# frontend/

The NiftyEdge Pro Trading Terminal UI — the **Presentation Layer** in `docs/Architecture/Architecture.md`.

## Current state

A static HTML/CSS/JS prototype (no build step, no framework) that renders every core screen with realistic dummy data. It is the visual and interaction spec for the target implementation: a **React** SPA talking to the FastAPI backend through the API Gateway (REST for actions, WebSocket for live ticks/option chain/orders) — see `docs/API/API.md`.

Migrating this prototype into React is close to a 1:1 port of structure: each `.html` page becomes a route/page component, `css/style.css`'s design tokens (including the three `data-theme` variants) carry over directly as the design system, and `js/app.js`'s behaviors (theme switching, tabs, clock, toggles) become small hooks/components backed by real API calls instead of hardcoded markup.

Open `index.html` directly in a browser, or serve the folder with any static file server:

```
npx serve .
```

## Pages

| File | Screen |
|---|---|
| `index.html` | Dashboard — AI market bias, price/value, OI walls, chart, strategy signal, option snapshot |
| `cpr-dashboard.html` | CPR Indicator pre-market report (shareable, always-light layout) |
| `market-profile.html` | TPO / Market Profile |
| `volume-profile.html` | Volume-by-price profile |
| `footprint.html` | Order flow / footprint chart |
| `options-chain.html` | Full calls/puts option chain |
| `open-interest.html` | OI buildup, OI by strike, max pain |
| `greeks.html` | Per-strike Greeks + portfolio risk |
| `strategy-signals.html` | AI signal feed + history |
| `positions.html` | Open/closed positions |
| `orders.html` | Order book + order entry |
| `backtester.html` | Strategy backtest configuration & results |
| `reports.html` | P&L statements, tax reports, charges |
| `settings.html` | Account, broker/API, risk limits, notifications, appearance |
| `help.html` | FAQ, guides, shortcuts, support |

## Shared assets

- `css/style.css` — design system: layout, components, and the three themes (`light` / `dark` / `terminal`), switched via `document.documentElement.dataset.theme`.
- `js/app.js` — clock, tab groups, toggle switches, and the theme switcher (persisted to `localStorage`).

## Known gaps before this becomes the real product frontend

- All data is hardcoded dummy data — needs to be wired to `backend/` REST endpoints (and a WebSocket feed for live ticks/option chain updates).
- No client-side routing/state framework — fine for a prototype, not for the real app once forms/order flows need validation and optimistic UI updates.
- No auth flow (login, session, 2FA) implemented yet.
- Charts are hand-authored SVG, not a charting library — swap for a real charting lib (e.g. lightweight-charts, D3, or a TradingView widget) when wiring to live data.
