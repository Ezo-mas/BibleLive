import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { PipelineBanner } from './PipelineBanner'
import { I18nProvider } from '../../shared/I18nProvider'
import { LANGUAGE_STORAGE_KEY } from '../../shared/i18n'
import type { PipelineStatus } from '../../shared/types'

/** The launcher's setting is not what these tests are about: no answer, English browser. */
beforeEach(() => {
  vi.stubGlobal('fetch', () => new Promise<Response>(() => {}))
})

afterEach(() => {
  window.localStorage.clear()
  vi.unstubAllGlobals()
})

function degraded(extra: Partial<PipelineStatus> = {}): PipelineStatus {
  return {
    status: 'degraded',
    language: 'lt',
    problems: ['The audio pipeline is quiet'],
    seconds_since_report: 2,
    ...extra,
  }
}

function renderIn(language: 'en' | 'lt', status: PipelineStatus | null) {
  window.localStorage.setItem(LANGUAGE_STORAGE_KEY, language)
  return render(
    <I18nProvider>
      <PipelineBanner status={status} />
    </I18nProvider>
  )
}

describe('PipelineBanner', () => {
  it('shows a known issue in Lithuanian, with its parameters filled in', () => {
    renderIn(
      'lt',
      degraded({
        issues: [
          {
            code: 'audio_stalled',
            params: { seconds: 42 },
            text: 'No audio has arrived from the microphone for 42 s',
          },
        ],
      })
    )
    expect(screen.getByText('Iš mikrofono garso nebegaunama jau 42 s')).toBeInTheDocument()
    expect(screen.queryByText(/No audio has arrived/)).not.toBeInTheDocument()
  })

  it('translates the thread role inside a thread problem', () => {
    renderIn(
      'lt',
      degraded({
        issues: [
          {
            code: 'thread_stopped',
            params: { role: 'transcription' },
            text: "The pipeline's transcription thread has stopped; restart the pipeline",
          },
        ],
      })
    )
    expect(
      screen.getByText('Sustojo garso apdorojimo atpažinimo dalis; paleiskite BibleLive iš naujo')
    ).toBeInTheDocument()
  })

  it('shows the server sentence for a code the console has no translation for', () => {
    renderIn(
      'lt',
      degraded({
        issues: [{ code: 'some_future_problem', params: {}, text: 'Something new went wrong' }],
      })
    )
    expect(screen.getByText('Something new went wrong')).toBeInTheDocument()
  })

  it('falls back to the plain problems when there are no structured issues', () => {
    renderIn('en', degraded({ issues: [], problems: ['Only this problem'] }))
    expect(screen.getByText('Only this problem')).toBeInTheDocument()
  })

  it('falls back to the plain problems when the server sends no issues at all', () => {
    renderIn('lt', degraded({ problems: ['Vėl tik tekstas'] }))
    expect(screen.getByText('Vėl tik tekstas')).toBeInTheDocument()
  })

  it('reads the offline age in Lithuanian', () => {
    renderIn(
      'lt',
      degraded({ status: 'offline', seconds_since_report: 5, problems: [], issues: [] })
    )
    expect(screen.getByText(/paskutinį kartą prieš 5 s/)).toBeInTheDocument()
  })

  it('renders nothing while the pipeline is healthy', () => {
    const { container } = renderIn('en', degraded({ status: 'ok', problems: [] }))
    expect(container).toBeEmptyDOMElement()
  })
})
