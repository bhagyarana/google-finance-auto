# Design Guide — Google Finance Bulk Upload

Reference: Midday.ai and similar minimal finance SaaS UIs (screenshots in project root).

---

## Philosophy

**Clean. Minimal. Trustworthy.**

Finance tools earn trust through clarity. Every pixel that isn't communicating data is noise. The UI should feel like a professional internal tool — not a marketing site.

Principles:
- Light background, dark text. No dark mode by default.
- Color is reserved for meaning (green = positive, red = negative, amber = warning).
- No decorative gradients, glows, or shadows for their own sake.
- Numbers are always tabular-figures, monospace-friendly.
- Whitespace is intentional — spacing creates hierarchy, not borders.

---

## Color Tokens

```css
/* Backgrounds */
--bg:           #f9fafb;   /* page background */
--surface:      #ffffff;   /* cards, panels */
--surface-hover:#f3f4f6;   /* row/item hover */

/* Borders */
--border:       #e5e7eb;   /* default border */
--border-light: #f3f4f6;   /* subtle dividers */

/* Text */
--text:         #111827;   /* primary */
--text-2:       #374151;   /* secondary */
--text-muted:   #6b7280;   /* labels, captions */
--text-disabled:#9ca3af;   /* disabled states */

/* Brand / Accent */
--accent:       #18181b;   /* buttons, active states */
--accent-hover: #27272a;

/* Semantic */
--green:        #16a34a;
--green-bg:     #f0fdf4;
--green-border: #bbf7d0;

--yellow:       #b45309;
--yellow-bg:    #fffbeb;
--yellow-border:#fde68a;

--red:          #dc2626;
--red-bg:       #fef2f2;
--red-border:   #fecaca;

--blue:         #2563eb;   /* links, income amounts */
--blue-bg:      #eff6ff;
```

---

## Typography

```css
font-family: 'Inter', system-ui, -apple-system, sans-serif;
font-size:   14px;
line-height: 1.5;

/* Scale */
--text-xs:   11px;   /* badges, captions */
--text-sm:   12px;   /* table headers, labels */
--text-base: 14px;   /* body */
--text-lg:   16px;   /* card titles */
--text-xl:   20px;   /* page titles */
--text-2xl:  24px;   /* hero metrics */

/* Weights */
400  regular — body copy
500  medium  — table data, labels
600  semibold — headings, button labels
700  bold    — metric numbers

/* Numbers always use tabular figures */
font-variant-numeric: tabular-nums;
font-feature-settings: "tnum";
```

---

## Spacing

Base unit: `4px`. All spacing is a multiple of 4.

| Token | Value | Usage |
|-------|-------|-------|
| `--s1` | 4px | tight gaps, icon padding |
| `--s2` | 8px | inline gaps, badge padding |
| `--s3` | 12px | input padding, small gaps |
| `--s4` | 16px | card section gaps |
| `--s6` | 24px | card padding |
| `--s8` | 32px | section separation |

---

## Components

### Cards
```
background: var(--surface)
border:     1px solid var(--border)
border-radius: 8px
padding:    24px
box-shadow: none  ← no shadow by default
```

### Buttons

**Primary** (submit, run automation):
```
background: var(--accent) = #18181b
color: #ffffff
padding: 8px 16px
border-radius: 6px
font-weight: 600
font-size: 14px
hover: background #27272a
```

**Ghost** (back, secondary):
```
background: transparent
border: 1px solid var(--border)
color: var(--text)
hover: background var(--surface-hover)
```

**Danger** (clear auth):
```
background: transparent
border: 1px solid var(--red-border)
color: var(--red)
hover: background var(--red-bg)
```

### Form inputs
```
background: var(--surface)
border: 1px solid var(--border)
border-radius: 6px
padding: 8px 12px
font-size: 14px
color: var(--text)
focus: border-color var(--accent), outline 2px solid rgba(24,24,27,0.1)
```

### Badges / Status pills
```
display: inline-flex
padding: 2px 8px
border-radius: 4px    ← subtle radius, not fully round
font-size: 11px
font-weight: 600
letter-spacing: 0.3px
text-transform: uppercase

.badge-success: bg var(--green-bg), color var(--green), border var(--green-border)
.badge-warning: bg var(--yellow-bg), color var(--yellow), border var(--yellow-border)
.badge-error:   bg var(--red-bg),    color var(--red),    border var(--red-border)
.badge-neutral: bg var(--surface-hover), color var(--text-muted), border var(--border)
```

### Tables
```
Header row:
  background: var(--surface-hover)
  border-bottom: 1px solid var(--border)
  font-size: 11px
  font-weight: 600
  text-transform: uppercase
  letter-spacing: 0.6px
  color: var(--text-muted)
  padding: 8px 16px

Data row:
  border-bottom: 1px solid var(--border-light)
  padding: 10px 16px
  font-size: 14px
  hover: background var(--surface-hover)

Amounts:
  font-variant-numeric: tabular-nums
  Positive (income): color var(--green)
  Negative (expense): color var(--text)
```

### Dropzone
```
border: 1.5px dashed var(--border)
border-radius: 8px
background: var(--surface)
padding: 48px 24px
text-align: center

dragging: border-color var(--accent), background var(--surface-hover)
has-file:  border-color var(--green), border-style solid
```

### Progress / Log
```
background: var(--bg)        ← slightly off-white, not black terminal
border: 1px solid var(--border)
border-radius: 8px
font-family: 'Fira Code', 'Cascadia Code', monospace
font-size: 12px

Log line colors:
  info:    var(--text-muted)
  success: var(--green)
  warning: var(--yellow)
  error:   var(--red)
  done:    var(--text) font-weight 600
```

### Progress bar
```
track: height 3px, background var(--border)
fill:  background var(--accent)   ← solid black, no gradient
border-radius: 10px
```

---

## Layout

```
Page:
  max-width: 760px   ← narrower than before, more focused
  margin: 0 auto
  padding: 40px 24px 80px

Header:
  display: flex
  align-items: center
  margin-bottom: 32px
  padding-bottom: 20px
  border-bottom: 1px solid var(--border)

Steps:
  gap: 16px between cards
```

---

## Icons & Step indicators

### Header logo
Inline SVG only — no emoji, no icon fonts. The SVG must:
- Use `fill="none"` with `stroke="white"` on the dark `var(--accent)` background
- Be 16×16 viewBox, rendered at 16×16 inside the 32×32 container
- Use `stroke-linecap="round"` and `stroke-linejoin="round"` for a refined feel
- Carry `aria-hidden="true"` since the adjacent `<h1>` provides the accessible label

Current icon: upward-trending line with terminal dot (`polyline` + `circle`), representing portfolio performance.

### Step number indicators
Steps are indicated by a small circled number using text:
```
width: 20px; height: 20px
border: 1px solid var(--border)
border-radius: 50%
font-size: 11px
font-weight: 700
color: var(--text-muted)
background: var(--surface)
```

Active step: border-color and color use `var(--accent)`.

---

## What to avoid

- No gradients on interactive elements
- No colored backgrounds on cards (white only)
- No box-shadow on cards (use border instead)
- No all-caps for body text (only table headers, badge labels)
- No emoji anywhere in production UI — header logo included
- No animations longer than 150ms
- No `border-radius > 8px` on cards (12px+ feels consumer, not professional)
