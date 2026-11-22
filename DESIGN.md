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
| Badges, empty state | `frontend/src/components/*.css` |
| Row buttons (icon-only Remove) | `frontend/src/operator/components/RowButtons.tsx` |
| Layout and the console's own parts (header and its chips, the two cards, the panel, rows, dialog, search, settings rows) | `frontend/src/operator/styles/*.css` (base, header, layout, live-panel, side-panel, settings, scripture-search, settings-dialog, responsive) |

## Token-only rule

- No raw colour (`#hex`, `rgb()`, `rgba()`) outside `tokens.css`. Components and
  CSS files use `var(--color-*)` and the console surface tokens `--app-bg`,
  `--panel-bg`, `--card-bg`, `--accent-text`. Tints are
  `color-mix()` of a token with a surface token, never a second hex.
- No raw radius, height, weight or letter-spacing either: use the tokens below.
  The only literal sizes left across `frontend/src/operator/styles/*.css` are layout dimensions (column
  widths, card min-heights, breakpoints).
- A new palette is a swap of the values in `tokens.css`: the light and dark theme
  blocks plus the console blocks at the end (`.operator-app`, its dark variant, and
  the header band scope). The console uses the launcher's warm palette; gold
  (`#f7bc62`) is the primary action, the highlighted row (the keyboard cursor), the
  follow-along tint and the On screen tag, and nothing else
  is a hue except the danger Remove and the warning chips and ring.
- Every change must work in light and dark (`useTheme`, `data-theme`). Check both.
- WCAG AA contrast, visible focus (the global `:focus-visible` ring, drawn in
  `--color-border-focus`), `aria-label` on icon-only buttons, and
  `prefers-reduced-motion` (handled in `global.css`).

## Layout

Two columns inside the 16px page gutter, which is the only space at the window's
sides: nothing is centred and nothing has a maximum width. Right: one tabbed panel
(Pending, History) pinned to the right edge at `clamp(368px, 34vw, 560px)`
(`--operator-side-w`), filling the height. Left, top to bottom: the ON DISPLAY card
(the preview with its controls in the same card; follow-along is a strip laid over the
top of the preview, not a card of its own), then the Plan card, always in view. The
column takes every pixel of width the panel does not (`flex: 1`, `min-width: 0`). The
gear in the header opens Settings, which holds everything not needed mid-service
(language, Bible version, interface language, follow-along switch, the auto-approve
modes, display verse limit and background).

**Stacked layout.** The two columns hold down to 1024px (the left column is then
608px), so a laptop or a tablet held landscape keeps the queue, its footer, the Plan
and the preview in view together. Below 1024px it is one column in the order ON
DISPLAY, Plan, Pending / History; the page scrolls, and the preview takes its height
from 16:9. The Plan and the tabbed panel are each one fixed height there: the Plan
`min(420px, the visible height of the page)`, the panel `min(520px, the visible
height)`, both with a 280px floor, so each list scrolls inside its card and the page
does not change length as detections arrive or passages are planned. The scrolling
column is a size container (`cqh`) and positioned, so nothing in it lengthens the
document behind it.

**Heights.** The left column is a flex column of the full height. The Plan is a share
of it, `clamp(216px, 48%, 380px)`, whatever it holds (its rows scroll inside it); ON
DISPLAY takes all the height left (`flex: 1`, with a floor of `--operator-frame-min`,
160px, for the monitor frame: a window shorter than that scrolls the column).
The left column's last card ends at the same height as the right panel.

**The preview.** Inside ON DISPLAY, the monitor frame (`.operator-monitor-wrap`)
spans the card's inner width (12px inset) and fills the leftover height. The
miniature of the audience screen uses the display's theme (`data-theme`) and
its own 1px border. It fills the frame edge to
edge: no bars around it, at the cost of not always being the projector's 16:9 (on a
two-column window it is usually wider; in the one-column layout the frame is 16:9, so
it is exact). It is a size container, and everything inside scales with the tighter
of width and height (`min(cqw, cqh)`), like a 1920x1080 screen shrunk to fit. Its text
is fitted the way the display fits its own (`usePreviewFit`, reusing `fitText.ts`:
shrink until the verses fit, bounds scaled from the display's by that same tighter
side), so ten verses or all of
them shrink to fit; if they ever overflow they start at the top, never a clipped
middle. With nothing on screen it shows the idle picture the way the display does
(Logo contained in half the width and 40% of the height, Fill covering it), or the
empty message when no picture is set. It is presentational only.

**Follow-along overlay.** The follow-along strip (`.operator-follow`) is positioned
absolutely inside the monitor frame, 8px from its top and sides: label, reference, the
start of the verse on one line, Show at the right (a 36px control, so the strip is
48px tall). It takes no space, so when it appears or goes nothing in the page moves or
resizes: the controls, the Plan, the miniature and the tabs panel stay exactly
where they are. The cost is that it covers the top of the miniature while it shows
(about 56px of it, usually padding, but the first line of a passage that fills the
screen). It is a neutral raised surface with a trace of the accent and a shadow, so it
reads as laid over the preview, and it takes no keyboard shortcut. The frame never goes
under its 160px floor, below which the column scrolls instead.

## Header

One slim bar: the brand, the Find Scripture command bar, then at the right the
Connecting warning, Speech and Settings (and Logout when a token is in use).
The brand and right controls keep their intrinsic width while Find Scripture
shrinks. At 800px and below, search moves to its own row. In both themes the bar
is a dark band (`#221e1a`) with light text, a dark search field (`#2f2a24`) and a
gold focus ring (`#ffc879`); the search results and the action feedback that hang
from it keep the page's colours.

- **Operator connection.** The warning chip says "Connecting…" during the first
  connection or a manual Reconnect. The banner below the bar reports a closed
  socket. Screen readers get the connection state through a visually hidden
  "Connection status: …" message.
- **Speech** is a read-only status with a muted language code such as "EN" or
  "LT" and five bars showing the latest 200 ms microphone peaks. The bars
  use the secondary text, success, warning and danger colours. The waveform
  stays static while offline, and a reported silent microphone adds a warning
  outline and tint. Screen readers get the full language name and offline
  status. The tooltip also shows the measured input and recent peak.
- **Settings** uses a 36px ghost button with the shared hover fill and keyboard
  focus ring. The spoken-language picker lives under Audio in the dialog.

## Cards share one anatomy

On display and Plan use the same sentence-case, base-size semibold section
heading. Their header actions sit at the right. Plan has a count beside its
heading and a hairline divider over the list.

The On display header holds Clear and the Live / Off air switch. When the
connected server confirms zero audience displays, a "No display connected"
text-only warning chip sits next to the switch and describes it for screen readers.
Clear uses the subtle neutral button variant, the accessible name "Clear display"
and the B shortcut. A neutral badge beside Clear shows the confirmed auto-clear
duration in seconds while an enabled timer is running on an on-air passage.
The switch is a ghost button with a filled gold dot and bold
label when Live, and a hollow neutral dot and medium label when Off air. It has
the shared hover fill and focus ring. Off air, the preview keeps showing the
passage, dimmed and grey but still readable, with no ring or tag. Screen
readers get "The audience sees a black screen". The switch is a
plain state-named button, not an `aria-pressed` toggle: its accessible name is
"Live" or "Off air", and the hover title names the action, so off air it reads
"Off air, button, Show the display to the audience again".

## Panel menus

Plan opens its three-dot menu beside the trigger, within the header and above
the row controls. The Pending and History triggers sit at the footer's left
edge and open upward. All three use a ghost trigger, a raised surface, and the
shared focus ring. Clear plan, Clear queue and Clear history use an × and danger
text inside their menus. The Pending menu
also shows the Auto-approve toggle and its On or Off state. Turning it back
on restores the last active mode from the current console session, defaulting
to High only. While active, the mode appears in a left-aligned text line
directly below the tabs, including in History. The Pending footer shows the
navigation hint when the queue has rows and keeps its menu available when empty.

## One spacing system

Card padding uses `--card-pad` (12px) and `--card-gap` (8px) from
`tokens.css`. The tab bar and card headers align across columns. Tabs
share the bar equally. List rows use tighter spacing so several entries
and their actions fit on a short screen. The Pending and Plan empty states
center the icon vertically beside the message. Pending uses a single sentence.

## Buttons

One box for all: `1px` border, `--radius-control`, semibold label. Variants only
change colours, so every button in a row has the same size.

| Variant | Look | Use |
| --- | --- | --- |
| `primary` | gold fill and darker edge | Add to Plan in search, Connect |
| `secondary` | raised surface and neutral outline | Reconnect, Logout |
| `subtle` | neutral fill and transparent border | Clear display |
| `danger` | raised surface with danger border and text | Remove the idle picture |
| `ghost` | transparent at rest with hover fill | Settings, Live toggle, panel menus, row trash and reorder buttons, dialog close, toast dismiss |

The resolved search row surrounds the passage button and the + action with a
`--color-primary-subtle` fill and a primary border. `iconOnly` gives a square
button and needs an `aria-label`. Sizes (`--control-h-*`): `sm` 30px for dense rows
(list-row buttons, banners, popover); `md` 36px is the default (header, the cards,
dialogs); `lg` 44px for bars and the sign-in button. Use `<Button>`; only list-style
buttons with custom inner layout (search results) use the classes directly.

## Rows: Pending, History and Plan

**One keyboard cursor.** A click on a row (anywhere but its buttons), or focus on it,
highlights it and sends nothing. Enter displays it. There is one
highlighted row, in Pending or in Plan, whichever was last clicked or focused; ↑ ↓
move it within that list, and focus moves with it so the row scrolls into view; Esc
drops it. A new detection never takes it, and Enter does nothing while the highlighted
row's tab is hidden (History open). History rows take no cursor. The highlighted row
uses a light gold tint and a gold edge (`--color-primary-edge`); in dark mode the
tint is fainter, so the row's muted text keeps its contrast.

**Pending and History** use boxed rows with a reference, source or Context badge
where applicable, and a muted excerpt clamped to two lines. Excerpts use the
translation's punctuation. The footer shows the timestamp, with a trash icon
beside it in Pending. Double-click a row to show it, or press Enter on the
highlighted Pending row or focused History row. While a tab has rows, its footer
keeps a short, muted shortcut hint ("Double-click / Enter to show") in view; it never
fades or moves. A double-click doesn't select the
word under the pointer; dragging still selects text.

**Detection confidence** appears as a dashed edge for Low, including while
selected. Medium and High use the regular row treatment. The word remains in the
accessible description and tooltip. The Context badge still appears where
applicable.

**Plan** uses thin separators and 8px of horizontal padding on every row.
Each position number sits on a neutral circle. The title sits above a one-line
excerpt. The matching passage gets an On screen tag when the server reports
an audience display connected.
The excerpt can continue across verse boundaries and ends with an ellipsis when
it runs out of room. The trash and reorder arrows form a
right-aligned group with equal spacing. The group drops below the excerpt on
narrow cards. Double-click a Plan row or press Enter on the highlighted passage to
show it. Plan has no hint of its own: the keys work the same in both lists, and the
Pending footer names them.

## Radius by element kind

| Kind | Token | Used for |
| --- | --- | --- |
| Control | `--radius-control` (8px) | buttons, inputs, segmented controls, list items, menu popovers, follow-along strip |
| Card | `--radius-card` (12px) | preview, Plan, side panel, toast |
| Dialog | `--radius-dialog` (12px) | settings dialog, sign-in card |
| Tag | `--radius-tag` (4px) | tags, status badges, keycaps, search highlight |
| Pill | `--radius-full` | header status chips, counts, dots, switch |

## Borders and elevation

- `--border-width` is always 1px (the active tab underline is 3px). A Low-confidence
  row's edge is dashed, the only dashed edge in the console.
- Secondary buttons use the strong neutral border. Subtle and ghost buttons
  have transparent borders at rest. All variants use the shared focus ring.
- Panel edges, dividers and floating surfaces use the hairline `--color-border`.
- Elevation is only `--shadow-*`, on floating surfaces (popover, toast, dialog,
  sign-in card). No coloured side stripes or inset-shadow accents anywhere.

## Settings dialog

One design for the whole dialog (`SettingsDialog.tsx`, styles in
`frontend/src/operator/styles/settings-dialog.css` and
`frontend/src/operator/styles/settings.css`).

- **Header row:** the title at the left, Close (a ghost icon button, 36px) at the right,
  one control tall under `--card-pad`, with a hairline under it.
- **Sections** are headed by the overline style of the card labels (xs, semibold,
  uppercase, `--tracking-caps`, muted): Display (verses, background, auto-clear, idle screen),
  Stream overlay (OBS), This console (app theme, interface language), Audio, Auto-approve, Keyboard
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
  background, language, verses, theme, picture fit) and `.btn` variants. The
  auto-clear row uses the same Off/On segmented control. While On, the next row
  uses the shared `.input` for 5–600 whole seconds alongside Save.
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
  `aria-pressed` or `aria-checked`.
- **Idle screen** is three rows in the same anatomy: the picture (Upload / Replace as
  secondary, Remove as danger, 36px), the fit (a segmented Logo / Fill screen), and a
  16:9 preview in its own row with the monitor's border and corners, the current state
  under it. Errors use the same notice style as the rest of the console.
- **Stream overlay (OBS)** is five segmented rows (position, band height, background,
  verse font, alignment), then a preview row and a fixed OBS link. The server
  saves the layout and pushes changes live to OBS. The preview
  (`.stream-overlay-preview`) is a 16:9 box with the idle preview's border and corners
  over a checkerboard of two surface tokens (the camera); inside, the real overlay page
  (`/display?mode=obs&preview=1`, no socket, sample passages on a loop) is drawn at
  1920x1080 and scaled to the box. The link row puts a read-only `.input` (mono, xs)
  and a secondary Copy button across the full row width.

## Type and spacing

- Sizes `--font-size-xs|sm|base|lg`; weights `--weight-regular|medium|semibold|bold`
  only. Body and controls are sentence case.
- Settings and the follow-along label use the small, muted uppercase overline.
- Spacing only from `--space-*` (4px grid, plus `--space-0-5` for 2px gaps).
- Icons are `--icon-sm` (16px) beside text and `--icon-md` (18px) in controls,
  banners and toasts.

## Verify in English and Lithuanian

Check both themes and interface languages in a browser. Verify that longer
Lithuanian labels fit beside the display controls and under the tabs, that
Find Scripture leaves room for the title and status controls, and that verse
ranges stay on one line in Plan and the queue.

## Adding something new

Reach for `<Button>`, `.segmented`, `.input`, `.tag` and the
radius roles first. If none fit, add a shared class here and document it in this
file, not a one-off in a component's CSS.
