import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

// Vitest runs from frontend/ (the documented command), so the path is relative to it.
const css = readFileSync('src/styles/tokens.css', 'utf8')

const consolePalette = css.slice(css.indexOf('/* Operator console palette'))

function block(selector: string): string {
  const start = consolePalette.indexOf(`${selector} {`)
  expect(start).toBeGreaterThanOrEqual(0)
  return consolePalette.slice(start, consolePalette.indexOf('}', start))
}

function color(rule: string, name: string): string {
  const value = rule.match(new RegExp(`--${name}: (#[0-9a-f]{6});`))?.[1]
  expect(value).toBeDefined()
  return value!
}

function contrast(a: string, b: string): number {
  const luminance = (hex: string) => {
    const channels = [1, 3, 5].map(index => {
      const value = parseInt(hex.slice(index, index + 2), 16) / 255
      return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
    })
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]
  }
  const [low, high] = [luminance(a), luminance(b)].sort((x, y) => x - y)
  return (high + 0.05) / (low + 0.05)
}

describe('operator palette', () => {
  it('keeps header text and controls legible on the band and search field', () => {
    const header = block('.operator-app .operator-header')
    for (const background of ['panel-bg', 'color-canvas']) {
      for (const foreground of ['color-text', 'color-text-secondary', 'color-text-muted']) {
        expect(
          contrast(color(header, foreground), color(header, background))
        ).toBeGreaterThanOrEqual(4.5)
      }
      for (const border of ['color-border-strong', 'color-border-focus']) {
        expect(contrast(color(header, border), color(header, background))).toBeGreaterThanOrEqual(3)
      }
    }
  })

  it('restores every band token on the header overlays in both themes', () => {
    const rules = [...consolePalette.matchAll(/([^{}]+)\{([^}]*)\}/g)].map(
      ([, selector, body]) => ({
        selectors: selector
          .replace(/\/\*[\s\S]*?\*\//g, '')
          .split(',')
          .map(part => part.trim()),
        props: new Set([...body.matchAll(/(--[\w-]+):/g)].map(match => match[1])),
      })
    )
    const band = rules.find(rule => rule.selectors.includes('.operator-app .operator-header'))
    expect(band).toBeDefined()
    for (const overlay of ['.operator-search-drop', '.operator-feedback']) {
      const on = (selector: string) => rules.filter(rule => rule.selectors.includes(selector))
      const derived = on(`.operator-header ${overlay}`).find(rule =>
        rule.props.has('--color-primary-hover')
      )
      const light = on(`.operator-header ${overlay}`).find(rule => rule !== derived)
      const dark = on(`[data-theme='dark'] .operator-header ${overlay}`)[0]
      expect(derived && light && dark).toBeTruthy()
      // In dark theme a token the dark rule leaves out would fall back to the light value.
      for (const [theme, restore] of [
        ['light', light!],
        ['dark', dark!],
      ] as const) {
        for (const prop of band!.props) {
          const restored = restore.props.has(prop) || derived!.props.has(prop)
          expect(restored, `${overlay} keeps the band's ${prop} in ${theme}`).toBe(true)
        }
      }
    }
  })
})
