# IMS frontend (shop app + POS)

React 19 + TypeScript + Vite PWA. The dev server proxies `/api` to the Django
backend on `127.0.0.1:8000` (same origin, so session cookies and CSRF work).

```bash
npm install
npm run dev      # http://localhost:5173
npm test         # vitest: pricing parity with the backend, input parsing, API errors, offline sync
npm run lint     # oxlint
npm run build    # tsc -b + vite build
```

Where things are and the conventions (money as integer paisa, `QtyInput` /
`MoneyInput`, `lib/pricing.ts` mirrors `backend/sales/pricing.py`) are in
`docs/CODE-GUIDE.md` at the repository root. `console/src/shared/` holds copies
of a few files from here: after changing `components/ui.tsx`, `components/Icon.tsx`,
`lib/api.ts`, `lib/money.ts` or `index.css`, run `npm run sync-shared` in `console/`.
