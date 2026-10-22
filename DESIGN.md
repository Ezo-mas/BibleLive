# DESIGN.md — operator console visual rules

How the operator page (`/operator`) is built so every control looks and behaves
the same. It describes what the code does. Read it before adding or restyling
anything in `frontend/src/operator/`; if a change needs a new look, extend a
shared class instead of writing a one-off. Display page styling is separate
(`frontend/src/display/display.css`) and only shares the tokens.

## Where things live

| What | File |
| --- | --- |
| Tokens: scale, radius roles, control heights, weights, and **all colours** (light, dark, and the console palette at the bottom) | `frontend/src/styles/tokens.css` |
| Buttons (`.btn` + variant + size) | `frontend/src/components/Button.css`, `Button.tsx` |
| Segmented control (`.segmented`), text field (`.input`), tag (`.tag`) | `frontend/src/styles/controls.css` |
| Badges, connection dot, empty state | `frontend/src/components/*.css` |
| Layout and the console's own parts (header, the three cards, panels, rows, dialog, search, settings rows) | `frontend/src/operator/operator.css` |

## Token-only rule

- No raw colour (`#hex`, `rgb()`, `rgba()`) outside `tokens.css`. Components and
  CSS files use `var(--color-*)` and the console surface tokens `--app-bg`,
  `--panel-bg`, `--card-bg`, `--accent-text`. Tints are
  `color-mix()` of a token with a surface token, never a second hex.
- No raw radius, height, weight or letter-spacing either: use the tokens below.
  The only literal sizes left in `operator.css` are layout dimensions (column
  widths, card min-heights, breakpoints).
- A new palette is a swap of the values in `tokens.css`: the light and dark theme
  blocks plus the two console blocks (`.operator-app`, dark variant) at the end.
  The console is amber on warm grey; amber is the primary action, the staged row
  and the follow-along tint, and nothing else is a hue except the danger Remove.
- Every change must work in light and dark (`useTheme`, `data-theme`). Check both.
- WCAG AA contrast, visible focus (the global `:focus-visible` ring, drawn in
  `--color-border-focus`), `aria-label` on icon-only buttons, and
  `prefers-reduced-motion` (handled in `global.css`).

## Layout

Two columns inside 16px of padding, centred on a window wider than the two together. Left, top to bottom: the ON DISPLAY preview
with its control row in the same card, UP NEXT, follow-along. Right: one tabbed
panel (Pending, Plan, History) that takes the width that is left, between 300px and
560px (`--operator-side-max`; it never balloons on a wide window), and fills the height. Below 1100px it is one column and the page scrolls. The gear in the header
opens Settings, which holds everything not needed mid-service (language, Bible
version, interface language, follow-along switch, the auto-approve modes, display
background).

The preview is a 16:9 miniature of the audience screen drawn in the display's own
theme (`data-theme`). It spans the card's inner width exactly, with the same 12px
inset as the control row under it, so it is never letterboxed; its text is fitted the way the display fits its own (`usePreviewFit`, reusing `fitText.ts`: shrink until the verses fit, bounds scaled from the display's), so ten verses or all of them shrink to fit; if they ever overflow they start at the top, never a clipped middle. It is presentational only. Spanning the width
makes the left column's width a function of the height: `.operator-main` is a size
container, and the column is `min(width left after the side panel's minimum,
max(--operator-column-min, (height - --operator-chrome) x 16/9 + card padding))`.
`--operator-chrome` (in `operator.css`) is the measured height of everything in the
column except the monitor, so if a card's anatomy changes, measure it again. A
window too short for the floor scrolls the column.

## Cards share one anatomy

ON DISPLAY, UP NEXT and follow-along use `.operator-card-head`: one group on the text
baseline (`.operator-card-title`: the 12px uppercase label, then the reference as
14px bold), centred against the card's actions at the right, with free space between.
ON DISPLAY has only its label; whether it is live is read from the monitor itself
and there is no Live chip. Text goes below in the muted serif (`.operator-card-verse`,
3 lines, 2 in follow-along). Same 12px side padding and 36px (`md`) controls, so the
preview, Up Next text and follow-along text share a left edge. The three are told
apart by treatment, not by badges: the preview is a raised card; UP NEXT has a
recessed fill and a dashed `--color-border-strong` edge with extra space above it
("queued, not live") and Unstage then Show on display in its header; follow-along is a
neutral raised surface with a trace of the accent (about 6% fill, 28% edge), normal
body colours, and only its label in `--accent-text`. The control row under the
monitor is three groups, each a caption over one 36px control on one baseline: Screen
(Clear display), Verses (3, 5, 10, All: the values the server accepts) and Auto-approve
(Off / On). The captions are short on purpose (Lithuanian uses "Automatinis" over the
control; the group's accessible name is the full phrase), and the row stays on one line
down to the 560px column floor in both languages. An empty preview centres its message
on both axes.

## One spacing system

Defined once in `tokens.css` and used by every card and row: `--card-pad` (12px) on
all four sides, a header row exactly one control tall (36px, so 48px with the top
padding, with or without actions), `--card-gap` (8px) from the header to the body and
between a row's parts, 12px under the last element, and 12px between stacked cards.
Cards differ only in text size and line count. The three left-column cards and the
side panel's rows use it; the control row under the preview is padded the same 12px,
with 36px controls. Cards are `flex: none`, never squeezed; the column scrolls
instead. The tab bar is also 48px, so the two columns' headers line up, and its tabs
share the whole bar equally with no outer padding or gaps.

## Buttons

One box for all: `1px` border, `--radius-control`, semibold label. Variants only
change colours, so every button in a row has the same size.

| Variant | Look | Use |
| --- | --- | --- |
| `primary` | filled `--color-primary`, edge `--color-primary-edge` (a darker mix) | The main action of a card or row: Show on display, Show, Connect |
| `secondary` | `--color-surface-raised`, border `--color-border-strong` | Default for any action: Unstage (Up Next), Clear display, Stage, Clear queue, Reconnect, gear, Logout |
| `danger` | secondary box, danger border and text | Removing a plan row |
| `ghost` | no box until hovered | Only inside something that already frames it: dialog close, toast dismiss, reorder arrows |

`is-on` on a secondary button marks an active state (the staged row's Stage button);
it uses `--color-primary-subtle` and a primary border. `iconOnly` gives a square
button and needs an `aria-label`. Sizes (`--control-h-*`): `sm` 30px for dense rows
(list-row buttons, banners, popover); `md` 36px is the default (header, the cards,
dialogs); `lg` 44px for bars and the sign-in button. Use `<Button>`; only list-style
buttons with custom inner layout (search results) use the classes directly.

## Rows in the side panel

Pending, Plan and History rows share `.ref-item`: a boxed item (hairline border,
`--card-bg`, `--radius-control`) holding the reference, the start of its text as a
muted quote clamped to two lines, and, for Pending and Plan, the row's buttons
(`.ref-item-actions`). Pending: Show (primary) and Stage. Plan: Show, Stage and
Remove (danger), with the reorder arrows at the top right; a click on the title
stages, a double-click shows. History is read-only (click stages, double-click
shows). The staged row takes the accent border and fill all round. Plan text comes
from what was saved with the item, or one read-only lookup when it is missing; the
row shows the reference alone until it loads. No row has a coloured side stripe, and
none is allowed.

**Detection confidence** is a chip with the word only, on one amber-and-grey ramp
told apart by fill weight: High is `--color-primary-subtle` with accent text and an
amber border; Medium is transparent with `--color-text-secondary` and a strong
outline; Low is a muted fill, muted text and a dashed border.

## Radius by element kind

| Kind | Token | Used for |
| --- | --- | --- |
| Control | `--radius-control` (8px) | buttons, inputs, segmented controls, list items, follow-along strip |
| Card | `--radius-card` (12px) | the three cards, side panel, popover, toast |
| Dialog | `--radius-dialog` (12px) | settings dialog, sign-in card |
| Tag | `--radius-tag` (4px) | tags, status badges, keycaps, search highlight |
| Pill | `--radius-full` | confidence chips, counts, dots, switch |

## Borders and elevation

- `--border-width` is always 1px (the active tab underline is 2px; UP NEXT's dashed
  edge is 1.5px).
- A control you can act on is outlined with `--color-border-strong` at rest.
  `--color-primary` marks the selected or focused thing. Never use `none` on an
  action button.
- Panel edges, dividers and floating surfaces use the hairline `--color-border`.
- Elevation is only `--shadow-*`, on floating surfaces (popover, toast, dialog,
  sign-in card). No coloured side stripes or inset-shadow accents anywhere.

## Settings dialog

One design for the whole dialog (`SettingsDialog.tsx`, styles in `operator.css`).

- **Header row:** the title at the left, Close (a ghost icon button, 36px) at the right,
  one control tall under `--card-pad`, with a hairline under it.
- **Sections** are headed by the overline style of the card labels (xs, semibold,
  uppercase, `--tracking-caps`, muted): Display (verses, background, idle screen),
  This console (app theme, interface language), Audio, Auto-approve, Keyboard
  shortcuts.
- **Every setting is one `.operator-setting` row:** the label (semibold) with a muted
  hint under it on the left, the control on the right and centred against them, a 1px
  divider between rows (none after a section's last row), `--card-pad` (12px) padding
  top and bottom. The same label, hint and control alignment in every row. The row wraps:
  the label and hint are one block (`.operator-setting-text`) that is never narrower
  than 14rem (224px), the control keeps its own width, and a control that does not fit
  beside the text (a long label, another language, a narrow dialog) drops under it, to
  the start, instead of squeezing the text into one word per line. Anything else in the
  row (an error, a picture) spans the full width. The idle picture's preview is centred
  in its row with its caption centred under it.
- **Controls are only the shared ones:** `.segmented` for every choice (Off/On,
  background, language, verses, theme, picture fit) and `.btn` variants.
- **Segmented anatomy:** no inset or gap; options sit edge to edge inside a 1px border
  with a 1px divider between them, the track clips them, and the selected option fills
  its cell. The track keeps its own width (an inline grid), every option in one control
  is the same width (the widest label plus 8px each side sets it, never less than
  `--segment-w`, 44px), so
  the track is exactly the sum of its options plus the border. A 36px track has 34px
  options. The selected option is also bold (not colour alone); each label carries a
  hidden bold copy of itself (`SegmentedOption`), so selecting never changes a track's
  width. The focus ring is the normal ring on unselected options and the inverse text
  colour inside the selected fill, so it contrasts with both. State comes from
  `aria-pressed` or `aria-checked`. Auto-approve under the
  preview is an Off / On segmented control: On restores the last mode that was on (High
  until one has been chosen, in memory only), Off sends `off`. A caption over a
  control aligns the control to its start, and the Auto-approve group at the right edge
  aligns to the end, so a column's default never stretches the track.
- **Idle screen** is three rows in the same anatomy: the picture (Upload / Replace as
  secondary, Remove as danger, 36px), the fit (a segmented Logo / Fill screen), and a
  16:9 preview in its own row with the monitor's border and corners, the current state
  under it. Errors use the same notice style as the rest of the console.

## Type and spacing

- Sizes `--font-size-xs|sm|base|lg`; weights `--weight-regular|medium|semibold|bold`
  only. Body and controls are sentence case.
- The overline style (xs, semibold, uppercase, `--tracking-caps`, muted) is for card
  labels and the control-row labels. Nothing else is uppercase.
- Spacing only from `--space-*` (4px grid, plus `--space-0-5` for 2px gaps).
- Icons are `--icon-sm` (16px) beside text and `--icon-md` (18px) in controls,
  banners and toasts.

## Verify in English and Lithuanian

Every layout is verified in English and in Lithuanian, in light and dark, before it is
called done: Lithuanian labels are routinely 30 to 60% longer. Switch the language
through the real setting (Settings, Interface language) and measure in a browser, not
by eye: no wrapped row or stray line (the control row is one line down to the 560px
column floor), no horizontal overflow, a label column that never drops under 200px, and
the same alignment as English. A new string is checked for width where it sits: the
control row's captions and Off / On labels are pinned by a unit test
(`shared/quickRow.test.ts`), and a longer translation there needs a conscious choice and
a browser check. Layouts that depend on the length of text wrap or shrink (`flex-wrap`,
`minmax`), they never assume English.

## Adding something new

Reach for `<Button>`, `.segmented`, `.input`, `.tag` and the
radius roles first. If none fit, add a shared class here and document it in this
file, not a one-off in a component's CSS.
