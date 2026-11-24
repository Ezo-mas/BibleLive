import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

// Vitest runs from frontend/ (the documented command), so paths are relative to it.
const launcherCss = readFileSync('src/launcher/launcher.css', 'utf8')
const tokens = readFileSync('src/styles/tokens.css', 'utf8')

function withoutComments(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, '')
}

describe('launcher palette', () => {
  it('keeps raw colours out of launcher.css', () => {
    const css = withoutComments(launcherCss)
    expect(css.match(/#[0-9a-fA-F]{3,8}\b/g) ?? []).toEqual([])
    expect(css.match(/\b(?:rgb|rgba|hsl|hsla|hwb|lab|lch|oklab|oklch)\(/g) ?? []).toEqual([])
  })

  it('defines every colour token launcher.css reads', () => {
    const palette = tokens.slice(
      tokens.indexOf('/* Launcher palette'),
      tokens.indexOf('/* Operator console palette')
    )
    expect(palette.length).toBeGreaterThan(0)
    const used = new Set(
      [...withoutComments(launcherCss).matchAll(/var\((--(?:color-|accent-text)[\w-]*)/g)].map(
        match => match[1]
      )
    )
    expect(used.size).toBeGreaterThan(0)
    for (const name of used) {
      expect(palette, name).toContain(`${name}:`)
    }
  })

  it('loads the tokens and scopes them to the launcher page', () => {
    expect(readFileSync('src/launcher/main.tsx', 'utf8')).toContain("'../styles/tokens.css'")
    expect(readFileSync('launcher.html', 'utf8')).toMatch(/<html[^>]*class="bl-launcher-page"/)
  })
})
