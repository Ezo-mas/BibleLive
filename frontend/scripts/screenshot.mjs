/**
 * Visual check for the audience display.
 *
 * Starts nothing itself: run the server first, then
 *   PYTHONPATH=backend BIBLELIVE_PORT=8099 python3 -m core.server
 * and drive it with:
 *   node scripts/screenshot.mjs [--url http://127.0.0.1:8099] [--out /tmp/biblelive-shots]
 *
 * It sends an operator action over the real WebSocket so the display renders a
 * genuine server state, then captures PNGs in both themes. Layout problems like
 * ragged verse starts are only visible in a real render, which jsdom cannot do.
 */
import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import WebSocket from 'ws'
import { chromium } from 'playwright'
const args = process.argv.slice(2)
const readArg = (name, fallback) => {
  const index = args.indexOf(`--${name}`)
  return index === -1 ? fallback : args[index + 1]
}

const baseUrl = readArg('url', 'http://127.0.0.1:8099')
// Default outside the repo so a run does not leave untracked PNGs behind.
const outDir = path.resolve(readArg('out', '/tmp/biblelive-shots'))
const base = baseUrl.replace(/^http/, 'ws')

/** POST JSON and parse the reply, surfacing server-side errors. */
async function postJson(route, body) {
  const response = await fetch(`${baseUrl}${route}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  })
  const text = await response.text()
  if (!response.ok) {
    throw new Error(`${route} -> ${response.status}: ${text}`)
  }
  return text ? JSON.parse(text) : {}
}

/** Ask the server to queue a reference, then approve it onto the display. */
async function showReference(reference) {
  const submitted = await postJson('/api/reference', {
    book: reference.book,
    chapter: reference.chapter,
    verse_start: reference.verse_start,
    verse_end: reference.verse_end,
    confidence: 'high',
    inferred: false,
  })
  if (!submitted.reference_id) {
    throw new Error(`queue failed: ${JSON.stringify(submitted)}`)
  }
  const approved = await postJson('/api/approve', {
    reference_id: submitted.reference_id,
  })
  if (approved.status !== 'success') {
    throw new Error(`approve failed: ${JSON.stringify(approved)}`)
  }
  return approved
}

/** Change the display background the way the operator console does. */
function setTheme(theme) {
  return new Promise((resolve, reject) => {
    const socket = new WebSocket(`${base}/ws/operator`)
    const timer = setTimeout(() => {
      socket.close()
      reject(new Error('operator socket timed out'))
    }, 8000)
    // Operator actions are flat objects with an `action` field, not enveloped in
    // a `type`/`data` pair like the broadcasts are.
    socket.on('open', () => socket.send(JSON.stringify({ action: 'set_display_theme', theme })))
    socket.on('message', (raw) => {
      let payload
      try {
        payload = JSON.parse(raw.toString())
      } catch {
        return
      }
      if (payload.type === 'display_settings_confirmed' && payload.data.theme === theme) {
        clearTimeout(timer)
        socket.close()
        resolve(payload.data)
      }
      if (payload.type === 'error') {
        clearTimeout(timer)
        socket.close()
        reject(new Error(`set_display_theme: ${payload.data?.message}`))
      }
    })
    socket.on('error', (error) => {
      clearTimeout(timer)
      reject(error)
    })
  })
}

const passage = {
  book: 'John',
  chapter: 3,
  verse_start: 16,
  verse_end: 18,
}

// Passages chosen to exercise alignment: one long verse, mixed widths, and a
// range that crosses from one to two digit numbers.
const passages = [
  { label: 'john-3-16-18', book: 'John', chapter: 3, verse_start: 16, verse_end: 18 },
  { label: 'psalm-119-1-9', book: 'Psalms', chapter: 119, verse_start: 1, verse_end: 9 },
  { label: 'gen-1-1-3', book: 'Genesis', chapter: 1, verse_start: 1, verse_end: 3 },
  { label: 'matt-5-1-12', book: 'Matthew', chapter: 5, verse_start: 1, verse_end: 12 },
]

const viewports = [
  { name: 'projector-1920x1080', width: 1920, height: 1080 },
  { name: 'projector-1280x720', width: 1280, height: 720 },
]

await mkdir(outDir, { recursive: true })

const browser = await chromium.launch()
const written = []
const findings = []
const flashResults = []
const syncResults = []

try {
  for (const theme of ['dark', 'light']) {
    await setTheme(theme)
    for (const viewport of viewports) {
      const page = await browser.newPage({
        viewport: { width: viewport.width, height: viewport.height },
      })
      for (const item of passages) {
        await showReference(item)
        await page.goto(`${baseUrl}/display`, { waitUntil: 'networkidle' })
        // The fade swap runs at 400ms; wait for the verse block to be non-empty.
        await page.waitForFunction(
          () =>
            (
              document.querySelector('[data-testid="display-verses"]')?.textContent ?? ''
            ).trim().length > 0,
          { timeout: 10000 },
        )
        await page.waitForTimeout(700)

        const file = path.join(outDir, `${item.label}-${viewport.name}-${theme}.png`)
        await page.screenshot({ path: file })
        written.push(file)

        const report = await page.evaluate(() => {
          const rows = [...document.querySelectorAll('.display-verse')]
          // Each verse is a `display: contents` wrapper, so measure the number and
          // text spans: a common left edge on both is a clean hanging indent.
          const boxes = rows
            .map((row) => {
              const number = row.querySelector('.display-verse-number')
              const text = row.querySelector('.display-verse-text')
              if (!number || !text) return null
              return {
                verse: number.textContent.trim(),
                numberLeft: Math.round(number.getBoundingClientRect().left),
                textLeft: Math.round(text.getBoundingClientRect().left),
              }
            })
            .filter(Boolean)

          const block = document.querySelector('[data-testid="display-verses"]')
          const inner = block?.querySelector('.display-verses-inner')
          const needed = inner?.getBoundingClientRect().height ?? 0
          const available = block?.clientHeight ?? 0
          const textLefts = [...new Set(boxes.map((b) => b.textLeft))]

          return {
            theme: document.documentElement.getAttribute('data-theme'),
            background: getComputedStyle(document.querySelector('.display-app'))
              .backgroundColor,
            fontSize: block ? getComputedStyle(block).fontSize : null,
            verses: boxes,
            verseCount: boxes.length,
            // Non-null means the ragged-start bug is back.
            raggedStart:
              textLefts.length > 1
                ? { distinctTextLefts: textLefts.length, values: textLefts }
                : null,
            overflow: needed > available + 1 ? { needed, available } : null,
          }
        })
        await writeFile(
          file.replace(/\.png$/, '.json'),
          `${JSON.stringify(report, null, 2)}\n`,
        )
        findings.push({ file: path.basename(file), ...report })
      }
      await page.close()
    }

    // Two separate things worth checking, both only visible in a real browser:
    //  1. Reload must not flash. The prepaint script in display.html reads the
    //     stored theme, so a projector reloading on light should paint light on
    //     its very first frame with no ramp.
    //  2. A theme change while the page is open must apply live, which is the
    //     operator-to-projector sync path.
    flashResults.push(await checkNoFlashOnReload('dark'))
    flashResults.push(await checkNoFlashOnReload('light'))
    syncResults.push(await checkLiveThemeSync())
  }
} finally {
  await browser.close()
}

for (const f of findings) {
  const problems = []
  if (f.raggedStart) problems.push(`ragged start: ${f.raggedStart.values.join(', ')}`)
  if (f.overflow) problems.push(`overflows: ${JSON.stringify(f.overflow)}`)
  console.log(
    `${problems.length ? 'FAIL' : 'ok  '} ${f.file}  theme=${f.theme} bg=${f.background} verses=${f.verseCount} font=${f.fontSize}${
      problems.length ? `  ${problems.join(' | ')}` : ''
    }`,
  )
}

console.log(`\n${written.length} screenshots written to ${outDir}`)
const failed = findings.filter((f) => f.raggedStart || f.overflow).length
console.log(failed === 0 ? 'All renders aligned and fitting.' : `${failed} render(s) need attention.`)

for (const result of flashResults) {
  console.log(
    `${result.ok ? 'ok  ' : 'FAIL'} no-flash-on-reload: ${result.theme} -> ${JSON.stringify(result.observed)}`,
  )
}
for (const result of syncResults) {
  console.log(
    `${result.ok ? 'ok  ' : 'FAIL'} live-theme-sync: ${JSON.stringify(result.steps)}`,
  )
}
const flashFailed = flashResults.filter((r) => !r.ok).length
const syncFailed = syncResults.filter((r) => !r.ok).length
if (failed || flashFailed || syncFailed) {
  console.log(
    `\n${failed} render issue(s), ${flashFailed} flash issue(s), ${syncFailed} sync issue(s).`,
  )
  process.exitCode = 1
}

/** Record the theme and painted background once per animation frame. */
function startSampling(page) {
  return page.addInitScript(() => {
    window.__frames = []
    const sample = () => {
      const app = document.querySelector('.display-app')
      if (app) {
        window.__frames.push(
          `${document.documentElement.getAttribute('data-theme')}|${getComputedStyle(app).backgroundColor}`,
        )
      }
      if (window.__frames.length < 300) requestAnimationFrame(sample)
    }
    requestAnimationFrame(sample)
  })
}

/**
 * A projector that reloads while the operator has light selected should paint
 * light on the first frame. Seed the stored theme first, since that is what the
 * pre-paint script reads.
 */
async function checkNoFlashOnReload(theme) {
  await setTheme(theme)
  const context = await browser.newContext({ viewport: { width: 1280, height: 720 } })
  // Seed before any document loads so the pre-paint script sees the theme.
  await context.addInitScript((seeded) => {
    window.localStorage.setItem('biblelive_display_theme', seeded)
  }, theme)
  const page = await context.newPage()
  await startSampling(page)
  await showReference(passages[0])
  await page.goto(`${baseUrl}/display`, { waitUntil: 'load' })
  await page.waitForTimeout(600)
  const frames = await page.evaluate(() => window.__frames)
  await context.close()

  const observed = [...new Set(frames)]
  const offTheme = observed.filter((f) => !isBackgroundFor(f, theme))
  return { theme, observed, ok: frames.length > 0 && offTheme.length === 0 }
}

/**
 * Operator flips the theme on a page that is already open; the projector must
 * follow without a reload.
 */
async function checkLiveThemeSync() {
  await setTheme('dark')
  const context = await browser.newContext({ viewport: { width: 1280, height: 720 } })
  await context.addInitScript(() => {
    window.localStorage.setItem('biblelive_display_theme', 'dark')
  })
  const page = await context.newPage()
  await showReference(passages[0])
  await page.goto(`${baseUrl}/display`, { waitUntil: 'load' })
  await page.waitForTimeout(400)

  const steps = []
  const read = async () => {
    const state = await page.evaluate(() => ({
      attr: document.documentElement.getAttribute('data-theme'),
      stored: window.localStorage.getItem('biblelive_display_theme'),
    }))
    steps.push(state)
    return state
  }

  const before = await read()
  await setTheme('light')
  // Wait for the socket broadcast, then let the 300ms transition finish.
  await page
    .waitForFunction(() => document.documentElement.getAttribute('data-theme') === 'light', {
      timeout: 5000,
    })
    .catch(() => {})
  await page.waitForTimeout(500)
  const after = await read()
  await context.close()

  return {
    ok: before.attr === 'dark' && after.attr === 'light' && after.stored === 'light',
    steps,
  }
}

/** True when a `theme|rgb(...)` frame shows a background consistent with theme. */
function isBackgroundFor(frame, theme) {
  const [attr, bg] = frame.split('|')
  if (attr !== theme) return false
  const channels = (bg.match(/\d+/g) ?? ['255', '255', '255']).map(Number).slice(0, 3)
  const bright = channels.some((c) => c > 60)
  return theme === 'dark' ? !bright : bright
}
