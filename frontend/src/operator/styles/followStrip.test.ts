import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

// Vitest runs from frontend/ (the documented command), so the path is relative to it.
const css = readFileSync('src/operator/styles/live-panel.css', 'utf8')

describe('follow-along strip styles', () => {
  // jsdom applies no stylesheets, so the component tests cannot see this. Without the
  // rule the strip falls below the preview as a full-width block and shifts the page
  // (lost once when the Up Next panel's stylesheet was deleted).
  it('lays the strip over the top of the preview, inside the positioned frame', () => {
    const start = css.indexOf('.operator-follow {')
    expect(start).toBeGreaterThanOrEqual(0)
    const rule = css.slice(start, css.indexOf('}', start))
    expect(rule).toContain('position: absolute;')
    expect(rule).toContain('top: var(--card-gap);')

    const wrap = css.slice(css.indexOf('.operator-monitor-wrap {'))
    expect(wrap.slice(0, wrap.indexOf('}'))).toContain('position: relative;')
  })
})
