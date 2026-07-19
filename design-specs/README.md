# HAULTRA — Screen Specs Handoff

Self-contained bundle. Open any `*.html` directly in a browser — all styles/tokens are local to this folder. These are **visual contracts**, not shippable code: build against the real design-system React components (`<Badge>`, `<Stat>`, `<DataTable>`, `<Card>`, `<Tabs>`, `<Button>`). `screens.css` mirrors those components 1:1 so the mocks match production pixel-for-pixel.

## Files
| File | Screen |
|---|---|
| `GradeBadges.html` | Grade badge system — A+ → UNGRADED, card + row sizes |
| `SetupsScreen.html` | Scanner — bucket tabs, regime meter, grade cards, empty state, mobile stack |
| `AccountScreen.html` | Performance hub — KPI tiles, positions, journal, Schwab states |
| `IntelScreen.html` | Morning macro — regime banner, AI briefing, liquidity, flows, calendar, news |
| `TodaysSetupsPanel.html` | Dense top-3 setups panel for the Terminal right rail |
| `styles.css` + `*.css` tokens | The design-system token layer (colors, type, spacing, fonts) |
| `screens.css` | Component classes built on tokens (the visual contract) |

Every screen file ends with an inline **SPEC NOTES** block (component mapping, breakpoints, rules).

---

## GRADE → TOKEN MAP
Font is **Rajdhani 700** at every size. Card size = 21–23px / min-width ~56–66px. Row size = 12px mono weight / min-width 38px.

| Grade | Class | Fill | Text | Border | Extra |
|---|---|---|---|---|---|
| **A+** | `.g-aplus` | `--grad-brand` | `--text-on-accent` | transparent | `--glow-accent` shadow **+ 2.4s pulse** — only tier that glows/animates |
| **A** | `.g-a` | `--grad-brand-soft` | `--orange-400` | `rgba(255,122,24,.6)` | — |
| **B+** | `.g-bplus` | `rgba(255,176,32,.14)` | `--warn-500` | `rgba(255,176,32,.42)` | amber |
| **B** | `.g-b` | `--surface-raised` | `--text-muted` | `--border` | quiet |
| **UNGRADED** | `.g-ungraded` | `--surface-inset` | `--text-disabled` | dashed `--border-subtle` | `—` glyph; dim whole row to `.72` |

### Rules
- Reserve the **pulse + glow for A+ only**. If everything glows, nothing does.
- A+ setup **cards** also get `border-color:--border-accent` + `--glow-accent`. Row-size A+ gets only a subtle left `--grad-brand` tint wash (no glow).
- Never place two A+ badges adjacent without whitespace.
- Unknown price levels render `—` in `--text-faint` — **never a fake number**.
- Ship as either a `grade` prop on `<Badge>` or a dedicated `<GradeBadge grade size />`.

### Status badge → `<Badge>` tone
`Ready` = positive · `Wait` = warning/neutral · `Do Not Enter` = negative.

### Swing-score meter fill
`--grad-brand` → degrades to `--warn-500` → `--ink-400` → `--neg-500` as the score drops.

---

## RESPONSIVE
The app shell is `grid: 220px sidebar / 1fr content` (no fixed max-width), so grids must account for the sidebar offset — window width is NOT content width.
- **Setups grid:** 3-col → **2-col ≤1180px** → 1-col ≤820px.
- **Account KPIs:** `repeat(6,1fr)` → `repeat(3,1fr)` ≤1100px.
- **Intel / Account body:** 2-col → 1-col ≤1100px.
- Harden headers with `min-width:0` + truncating text so a ticker can never overlap a price.

## DISCIPLINE FEATURES (intentional — keep)
- Setups empty state: **"No A+ Setups — Patience is a position."** Always offer a lateral action (View Forming), never a dead end.
- Account **Discipline** score (conic ring, banded green/amber/red) and **Trades Today X/3** slot chips — at 3/3 the value turns `--warn-500` and Terminal should block new orders.
- Journal rows carry an outcome rail + badge: **Rules held / Broke rule / Avoided**.
