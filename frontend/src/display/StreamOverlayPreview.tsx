import { useEffect, useState } from 'react'
import { useI18n } from '../shared/i18nContext'
import type { Language } from '../shared/i18n'
import type { BibleReference } from '../shared/types'
import { parseStreamOverlayOptions } from './streamOverlayOptions'
import { StreamBand } from './StreamBand'
import { useFadeSwap } from './useFadeSwap'

/** How long each sample passage stays up before the next one fades in. */
export const PREVIEW_HOLD_MS = 4000

type Sample = Pick<
  BibleReference,
  'book' | 'book_label' | 'chapter' | 'verse_start' | 'verse_end'
> & {
  verses: { verse: number; text: string }[]
}

// Bible text is not a console string, so the samples live here rather than in the
// message tables: one short passage and one that makes the band shrink its text.
const SAMPLES: Record<Language, Sample[]> = {
  en: [
    {
      book: 'John',
      chapter: 3,
      verse_start: 16,
      verse_end: 16,
      verses: [
        {
          verse: 16,
          text: 'For God so loved the world, that he gave his only begotten Son, that whosoever believeth in him should not perish, but have everlasting life.',
        },
      ],
    },
    {
      book: 'Psalms',
      chapter: 23,
      verse_start: 1,
      verse_end: 3,
      verses: [
        { verse: 1, text: 'The LORD is my shepherd; I shall not want.' },
        {
          verse: 2,
          text: 'He maketh me to lie down in green pastures: he leadeth me beside the still waters.',
        },
        {
          verse: 3,
          text: "He restoreth my soul: he leadeth me in the paths of righteousness for his name's sake.",
        },
      ],
    },
  ],
  lt: [
    {
      book: 'John',
      book_label: 'Jono',
      chapter: 3,
      verse_start: 16,
      verse_end: 16,
      verses: [
        {
          verse: 16,
          text: 'Dievas taip pamilo pasaulį, jog atidavė savo viengimį Sūnų, kad kiekvienas, kuris jį tiki, nepražūtų, bet turėtų amžinąjį gyvenimą.',
        },
      ],
    },
    {
      book: 'Psalms',
      book_label: 'Psalmynas',
      chapter: 23,
      verse_start: 1,
      verse_end: 3,
      verses: [
        { verse: 1, text: 'Viešpats yra mano ganytojas, man nieko netrūks.' },
        { verse: 2, text: 'Jis leidžia man ilsėtis žaliose ganyklose, veda prie ramių vandenų.' },
        { verse: 3, text: 'Jis atgaivina mano sielą, veda teisumo takais dėl savo vardo.' },
      ],
    },
  ],
}

function sampleReference(sample: Sample, index: number): BibleReference {
  return {
    ...sample,
    id: `preview-${index}`,
    confidence: 'high',
    inferred: false,
    text: sample.verses.map(v => v.text).join(' '),
    created_at: '2026-01-01T00:00:00Z',
  }
}

/**
 * The console's preview of the stream overlay (`/display?mode=obs&preview=1`, in an
 * iframe in Settings). It takes its layout from the URL like the real overlay, but
 * opens no socket (so the server never counts it as an audience display) and cycles
 * sample passages with the real fade, so the operator sees the band come and go.
 */
export default function StreamOverlayPreview() {
  const { language } = useI18n()
  const [options] = useState(() => parseStreamOverlayOptions(window.location.search))
  const { shown, visible, timing, swap } = useFadeSwap<BibleReference>()

  useEffect(() => {
    const samples = SAMPLES[language] ?? SAMPLES.en
    let index = 0
    swap(sampleReference(samples[0], 0))
    const timer = window.setInterval(() => {
      index = (index + 1) % samples.length
      swap(sampleReference(samples[index], index))
    }, PREVIEW_HOLD_MS)
    return () => window.clearInterval(timer)
    // `swap` is a fresh function each render but always does the same thing.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [language])

  return (
    <StreamBand
      reference={shown}
      visible={visible}
      timing={timing}
      verseLimit="all"
      options={options}
    />
  )
}
