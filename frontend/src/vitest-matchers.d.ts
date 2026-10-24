/* eslint-disable @typescript-eslint/no-empty-object-type, @typescript-eslint/no-unused-vars -- re-declares vitest's Assertion<R, T> so the matchers merge; the type parameters must match vitest's exactly */
// jest-dom's own vitest typings (types/vitest.d.ts) declare `Assertion<T = any>`,
// but vitest 5 declares `Assertion<R, T>`. The two do not merge, and skipLibCheck
// hides the mismatch, so expect(...) would lose the jest-dom matchers and tsc would
// fail. Re-declare the augmentation with vitest's current type parameters. The
// runtime matchers are added in src/test/setup.ts.
import type { TestingLibraryMatchers } from '@testing-library/jest-dom/matchers'
import type { expect } from 'vitest'

declare module 'vitest' {
  interface Assertion<
    R extends void | Promise<void> = void,
    T = unknown,
  > extends TestingLibraryMatchers<ReturnType<typeof expect.stringContaining>, R> {}
}
