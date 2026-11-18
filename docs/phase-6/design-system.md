# Phase 6 · Dashboard design system

Visual theme inspired by harrygeorge.design. **Only the theme is borrowed** (colour, type,
spacing, mood). The layout, components and content are this project's own: an operations
dashboard for an IT consultancy's AI assistant.

## What we take from the inspiration

| Theme trait | How the dashboard uses it |
|---|---|
| Strict monochrome; no brand accent colour | Grey scale only. Status is shown with text, weight and brightness. Colour is reserved for one semantic signal: urgent (P1/high), shown in a muted red. |
| Near-black canvas with slightly lifted rounded surfaces | Page `--bg`; panels `--surface` with a 1rem radius; inset rows `--surface-2` |
| One grotesk typeface at a single medium weight | Geist Sans 500 everywhere (free, SIL OFL; stands in for the paid PP Neue Montreal). Hierarchy comes from size and brightness, not weight. |
| Large headings with tight negative tracking | `h1` is about 2.25rem at -0.04em; small headings at -0.01em |
| Small, calm body copy | Body is 15px; meta and labels are 12–13px in muted grey |
| Emphasis by brightness ("bright words, dim rest") | Key figures are in `--text-strong`; context is in `--text-muted` |
| Parenthetical status labels: "(Online)" with a live clock | The top bar shows "(Live)" plus the current time in London, and the queue status |
| Hairline-divided rows with a "+" affordance | Tables and lists use 1px `--border` rules, with no zebra striping |
| Compact buttons with a "↪" glyph; one light primary, one dark secondary | Primary: light fill with dark text. Secondary: `--surface-2`. 3px radius. |
| Floating, rounded, translucent nav | A floating rounded sidebar panel (the layout is ours; the treatment is theirs) |
| Oversized cropped wordmark | The login page uses a large cropped "ASSISTANT" wordmark |

## Tokens

```css
--bg:          #121312;   /* page */
--surface:     #151615;   /* panels, cards */
--surface-2:   #1c1d1c;   /* inset rows, secondary buttons */
--border:      #222322;   /* hairlines */
--border-strong:#2e2f2e;
--text:        #d5d6d5;   /* body */
--text-strong: #f1f1f1;   /* headings, key figures */
--text-muted:  #9e9f9e;   /* meta */
--text-faint:  #676867;   /* de-emphasised */
--danger:      #e5786d;   /* urgent only */
--radius: 1rem;  --radius-sm: .5rem;  --radius-xs: 3px;
```

Light theme: the same scale inverted (`--bg #f1f1f1`, `--text-strong #151615`). It follows the
OS setting and can be toggled by hand.

## Dashboard information architecture

| Page | Content | Roles |
|---|---|---|
| Overview | KPIs (conversations, leads, qualified rate, bookings, open handovers), 14-day volume chart, handover queue, upcoming calls | all staff |
| Conversations | Filterable list; transcript with tool calls collapsed | manager, admin |
| Leads | Status filter, qualification fields, summary | manager, admin |
| Bookings | Upcoming and past discovery calls | all staff |
| Handovers | Queue with urgency; acknowledge | all staff |
| Tickets | Across clients, with status and priority | all staff |
| Settings | Versioned config: greeting, qualification, booking and handover rules. Draft, preview, publish. | read: manager+, publish: admin |
