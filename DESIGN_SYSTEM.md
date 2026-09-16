# MemoryOS — Design System

Current direction · 2026-09-15 · Quiet, Omi-first workspace.

The current web application supersedes the historical glass specification below. Use ivory (#f5f4ef) for the workspace, forest green (#183b2e) for navigation, green (#285c43) for primary actions, and cream (#fffefa) for surfaces. Text is dark green (#20372c); borders are subtle (#dddfd4). Use Georgia for editorial page headings and Trebuchet MS with Segoe UI fallback for controls/body. No remote fonts are required.

Desktop has a persistent left sidebar; narrow phones use a compact bottom navigation. Capture, Memories, and Ask each have a clear page introduction. Omi is the primary recording device; computer microphone controls remain collapsed inside an optional disclosure. Keep connection and recording actions distinct, with explicit state text. Do not animate audio activity without received-audio evidence. Preserve reduced-motion support, keyboard focus, and layouts without horizontal scrolling.

This direction is implemented for review, not yet a confirmed user preference.

## Historical specification (superseded for the web application)

Version 0.1 · 2026-07-04 · Apple Glass language.

## 1. Principles

1. **Content is the interface.** Transcripts, memories, answers — chrome
   recedes behind frosted glass.
2. **Depth over decoration.** Layers, blur, and soft light communicate
   hierarchy; never borders-on-borders or drop-shadow soup.
3. **Motion means something.** Animations confirm actions and guide the eye;
   nothing moves without a reason. Springs, not linear tweens.
4. **Calm by default.** Recording all day must feel ambient, not alarming.
   One accent at a time; whitespace is a feature.
5. **Degrade beautifully.** Loading, empty, and error states are designed,
   not leftover.

## 2. Foundations

### 2.1 Color tokens (dark mode — default)
```css
--bg-base:      #0a0c12;                     /* deep space */
--bg-glow-1:    rgba(91,140,255,.16);        /* aurora blue, radial */
--bg-glow-2:    rgba(168,120,255,.12);       /* violet, radial */
--glass:        rgba(255,255,255,.055);      /* card fill */
--glass-strong: rgba(255,255,255,.09);       /* hovered / active card */
--stroke:       rgba(255,255,255,.10);       /* hairline borders */
--text-hi:      #f2f5fa;
--text-mid:     #a7b0bf;
--text-low:     #5d6675;
--accent:       #6d9bff;                     /* actions, focus */
--accent-soft:  rgba(109,155,255,.18);       /* chips, glows */
--positive:     #43d9a3;
--warning:      #f5b054;
--danger:       #ff6b6b;
--rec:          #ff5d5d;                     /* recording pulse */
```
Light mode (Phase 2): same tokens remapped; components reference tokens only.

### 2.2 Glass recipe
```css
background: var(--glass);
backdrop-filter: blur(24px) saturate(160%);
border: 1px solid var(--stroke);
border-radius: 20px;               /* cards */  /* 14px controls, 28px sheets */
box-shadow: 0 8px 32px rgba(0,0,0,.35);
```
The page background carries two fixed radial glows (--bg-glow-*) so glass has
something to refract. Never stack more than two glass layers.

### 2.3 Typography
- Stack: `-apple-system, "SF Pro Display", "Segoe UI Variable", system-ui`.
- Display 28/34 semibold (screen titles) · Title 17/24 semibold (cards) ·
  Body 15/22 regular · Caption 12/16 medium, `--text-mid` ·
  Mono (timestamps/transcripts): `ui-monospace, "Cascadia Mono"` 13/20.
- Letter-spacing −0.2px on display sizes; never justify text.

### 2.4 Spacing & layout
4px base grid. Card padding 16–20px. Section gap 24px. Page gutter 16px
(mobile) / 24px (desktop). Content max-width 760px, centered. Floating nav
clears 88px bottom padding.

### 2.5 Iconography
Inline SVG, 1.5px stroke, round caps (SF-Symbols-like). Emoji only as
content (never as controls) in production surfaces.

## 3. Components

| Component | Spec |
|---|---|
| **Glass card** | Recipe above; hover: `--glass-strong` + translateY(−1px), 250ms spring. |
| **Floating nav** | Bottom-centered pill, glass, 3 items (Live · Memories · Ask); active item gets `--accent-soft` pill + label; inactive icons `--text-mid`. |
| **Primary button** | Accent fill, white text, radius 14px, press: scale .97 spring. |
| **Quiet button** | Glass fill, `--text-hi`; same press behavior. |
| **Chip** | 12px caption in `--accent-soft` / category-tinted glass, radius 999. |
| **Input / textarea** | Glass fill, hairline stroke, focus: accent ring `0 0 0 3px var(--accent-soft)`, no default outline. |
| **Timeline card** | Title (Title), date+duration (Caption), category chip, people chips, action-item count badge, 1-line summary (Body, `--text-mid`, ellipsis). |
| **Ask answer** | Answer in Body on glass; citations as tappable `[title · mm:ss]` chips beneath; excerpts collapsible. |
| **REC indicator** | 8px dot pulsing (opacity .3↔1, 1.2s) + mono timer. |
| **Skeleton loader** | Glass blocks with slow shimmer (1.6s), matching final layout exactly. |
| **Empty state** | Centered icon (soft glow), one-line headline, one-line hint, optional quiet CTA. Never a bare "no data". |
| **Error state** | Inline glass banner, `--danger` tint at 12%, plain-language message + retry. Never a raw exception. |
| **Toast** | Top-center glass pill, auto-dismiss 2.5s, spring in/out. |

## 4. Motion

- Spring standard: `cubic-bezier(.32,.72,.28,1)` 350ms (near-critically
  damped). Micro-presses 150ms. View transitions: fade+8px rise, 250ms.
- Live transcript lines enter with 12px rise + fade (stagger 40ms).
- Progress (polish/index): thin accent bar with soft glow, indeterminate
  shimmer when % unknown.
- Respect `prefers-reduced-motion`: replace movement with opacity fades.

## 5. Interaction patterns

- **Ask is a command bar**: focused via `/` key or nav; answer streams in.
- Destructive actions: confirm inline (button morphs to "Delete? ✓/✕"),
  never a browser `confirm()` in production UI.
- Editing (titles): click-to-edit inline with focus ring; Enter saves,
  Esc cancels.
- All async actions show optimistic or loading affordance within 100ms.

## 6. Accessibility

- Contrast ≥ 4.5:1 for text tokens on glass (verified against darkest bg).
- Full keyboard path: tab order follows visual order; visible focus ring
  (accent, 3px soft). Esc closes sheets.
- All controls have `aria-label`s; live transcript feed is `aria-live=polite`.
- Hit targets ≥ 40×40px. Reduced-motion + reduced-transparency fallbacks
  (solid `#141821` cards when `backdrop-filter` unsupported).

## 7. Responsive behavior

- ≤480px: single column, nav stays floating, cards full-bleed with 16px
  gutter, Ask bar docks above nav.
- ≥1024px: 760px column; timeline may show date rail on the left (Phase 2).
- Touch and pointer parity for every interaction.

## 8. Voice & copy

Calm, precise, second person. "Nothing here yet — record your first
memory." Never blame the user; errors say what happened and what to do.
