# Phase 7: Frontend Design System and Theme Support

## Summary

Established a modern, accessible design system with comprehensive theming support (light/dark/system), design tokens, reusable components, and SVG icons to replace emojis.

## Key Changes

### 1. Design Tokens System

**Created:** `frontend/src/styles/tokens.css` (180+ lines)

Comprehensive CSS custom properties covering:

**Spacing Scale:** 10 consistent spacing values (4px - 96px)  
**Border Radius:** 5 sizes from sm to full  
**Shadows:** 4 elevation levels with dark mode variants  
**Z-Index Levels:** 8 semantic layers for stacking context  
**Typography Scale:** 11 font sizes with semantic naming  
**Font Families:** System font stacks (sans, serif, mono)  
**Line Heights:** 6 semantic line-height values  
**Transitions:** 3 speed variants with easing  

**Semantic Color System:**
- Surface colors (canvas, surface, raised, overlay, muted)
- Content colors (text, secondary, muted, inverse)
- Border colors (default, strong, focus)
- Semantic colors (primary, success, warning, danger, info)
- Each with hover/active states where appropriate

**Complete theme support:**
- Light theme (default)
- Dark theme with adjusted colors and shadows
- Automatic system preference detection

### 2. Global Styles

**Created:** `frontend/src/styles/global.css`

Professional reset and base styles:
- Modern box-sizing reset
- Font smoothing for better rendering
- Reduced motion support (respects user preferences)
- Accessible focus styles with `:focus-visible`
- Typography hierarchy (h1-h6)
- Custom scrollbar styling (webkit + Firefox)
- Selection colors
- Screen reader only utility class

### 3. Theme Management Hook

**Created:** `frontend/src/hooks/useTheme.ts`

Full-featured theme system:
```typescript
const { theme, resolvedTheme, setTheme } = useTheme()
// theme: 'light' | 'dark' | 'system'
// resolvedTheme: 'light' | 'dark'
```

**Features:**
- Persists preference in localStorage
- Respects system `prefers-color-scheme`
- Listens for system theme changes when set to 'system'
- No flash of incorrect theme on load
- Applies theme via `data-theme` attribute on `<html>`
- Graceful fallback for environments without localStorage

### 4. SVG Icon Library

**Created:** `frontend/src/components/Icons.tsx`

Accessible, themeable SVG icons:
- `BookIcon` - Replaces 📖 emoji
- `ClockIcon` - Replaces 🕒 emoji  
- `SunIcon` - Light theme indicator
- `MoonIcon` - Dark theme indicator
- `MonitorIcon` - System theme indicator
- `CheckIcon` - Success/approval actions
- `XIcon` - Close/clear actions
- `AlertCircleIcon` - Warning states
- `InfoIcon` - Information states

All icons:
- Use `currentColor` for automatic theming
- Accept standard SVG props
- 24x24 viewBox for consistency
- 2px stroke width
- Round caps and joins


### 5. Reusable Components

#### Button Component
**Created:** `frontend/src/components/Button.tsx` + CSS

Professional button with variants:
- **Variants:** primary, secondary, danger, ghost
- **Sizes:** sm, md, lg
- **Features:** icon support, full-width option, disabled state
- **Interactions:** hover lift, active press, focus ring
- **Accessibility:** proper focus handling, disabled cursor

#### StatusBadge Component
**Created:** `frontend/src/components/StatusBadge.tsx` + CSS

Confidence and status indicators:
- **Variants:** success, warning, danger, info, neutral
- **Features:** semantic colors, uppercase styling, pill shape
- **Use cases:** confidence levels, reference context tags

#### EmptyState Component
**Created:** `frontend/src/components/EmptyState.tsx` + CSS

Empty queue/history states:
- **Features:** optional icon, title, description, action button
- **Styling:** centered, muted colors, generous padding
- **Replaces:** emoji-based empty states with SVG icons

#### ConnectionIndicator Component
**Created:** `frontend/src/components/ConnectionIndicator.tsx` + CSS

WebSocket connection status:
- **States:** connected, connecting, disconnected
- **Features:** animated pulse for "connecting", semantic colors
- **Modes:** compact (dot only) or full (dot + label)
- **Accessibility:** screen reader announcements

#### ThemeToggle Component
**Created:** `frontend/src/components/ThemeToggle.tsx` + CSS

Theme switcher control:
- **Options:** Light, Dark, System
- **Features:** icon + label, radio group semantics
- **Responsive:** hides labels on mobile (icons only)
- **Styling:** segmented control design, active state highlight

### 6. Component Organization

**Created:** `frontend/src/components/index.ts` - Central export for all components  
**Created:** `frontend/src/hooks/index.ts` - Central export for hooks

## File Structure

```
frontend/src/
├── styles/
│   ├── tokens.css          # Design tokens (180 lines)
│   └── global.css          # Global styles (160 lines)
├── hooks/
│   ├── useTheme.ts         # Theme management (100 lines)
│   └── index.ts
├── components/
│   ├── Icons.tsx           # SVG icons (150 lines)
│   ├── Button.tsx + .css
│   ├── StatusBadge.tsx + .css
│   ├── EmptyState.tsx + .css
│   ├── ConnectionIndicator.tsx + .css
│   ├── ThemeToggle.tsx + .css
│   └── index.ts

## Benefits

### Design Consistency
- Single source of truth for colors, spacing, typography
- Semantic tokens ensure visual coherence
- Easy to update globally (change token, update everywhere)

### Accessibility
- Focus-visible support (keyboard navigation)
- Reduced motion support
- Proper ARIA labels and roles
- Screen reader announcements
- High contrast in both themes

### Developer Experience
- Autocomplete for design tokens in CSS
- TypeScript types for all components
- Reusable primitives reduce duplication
- Clear component API with props

### Theming
- User preference persistence
- System theme detection
- No flash of incorrect theme
- Smooth transitions between themes
- All components theme-aware

### Performance
- CSS custom properties (no runtime JS)
- SVG icons (no icon font loading)
- Minimal component overhead
- Efficient re-renders

## Usage Examples

### Using Design Tokens in CSS

```css
.my-component {
  padding: var(--space-4);
  background: var(--color-surface-raised);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-md);
  transition: all var(--transition-fast);
}
```

### Using Components

```typescript
import { Button, StatusBadge, EmptyState, BookIcon } from '../components'

// Button with icon
<Button variant="primary" icon={<CheckIcon />}>
  Approve
</Button>

// Status badge
<StatusBadge variant="success">High Confidence</StatusBadge>

// Empty state
<EmptyState
  icon={<BookIcon />}
  title="No pending references"
  description="References will appear here when detected"
/>
```

### Using Theme Hook

```typescript
import { useTheme } from '../hooks'

function MyComponent() {
  const { theme, resolvedTheme, setTheme } = useTheme()
  
  return (
    <div>
      <p>Current theme: {theme}</p>
      <button onClick={() => setTheme('dark')}>Dark Mode</button>
    </div>
  )
}
```

## Files Created

**Styles:**
- `frontend/src/styles/tokens.css` (180 lines)
- `frontend/src/styles/global.css` (160 lines)

**Hooks:**
- `frontend/src/hooks/useTheme.ts` (100 lines)
- `frontend/src/hooks/index.ts`

**Components:**
- `frontend/src/components/Icons.tsx` (150 lines)
- `frontend/src/components/Button.tsx` + `.css`
- `frontend/src/components/StatusBadge.tsx` + `.css`
- `frontend/src/components/EmptyState.tsx` + `.css`
- `frontend/src/components/ConnectionIndicator.tsx` + `.css`
- `frontend/src/components/ThemeToggle.tsx` + `.css`
- `frontend/src/components/index.ts`

**Total:** ~1100 lines of production-ready design system code

## Next Steps

### Phase 8 - Operator Console Redesign
- Apply design system to operator UI
- Replace hard-coded styles with tokens
- Add theme toggle to header
- Replace emoji with SVG icons
- Use new component primitives
- Implement keyboard shortcuts

### Phase 9 - Audience Display Redesign
- Apply design system to display UI
- Add display-specific theme options
- Implement responsive typography
- Theme-aware transitions
- Test on projector displays

## Next: Phase 8

Operator Console Redesign - Modern layout, design system integration, keyboard navigation

```
