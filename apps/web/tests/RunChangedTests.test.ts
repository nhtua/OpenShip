import { describe, expect, it } from 'vitest'
import { main, planFor, webFilesUnder } from '../scripts/run-changed-tests.mjs'

// The commit gate narrows the web suite the same way the API launcher narrows
// the Python lane: related suites when the change is visible to the suite, the
// whole suite when changed source relates to nothing, and nothing at all only
// for a commit the suite cannot observe. `--explain` announces that decision
// and runs nothing.
describe('changed-file test selection for the web suite', () => {
  it('runs the suites a source change reaches', () => {
    expect(planFor(['apps/web/src/stores/taskProgress.ts'], 3)).toBe('changed')
  })

  it('runs everything when changed source relates to no suite', () => {
    expect(planFor(['apps/web/src/new/widget.ts'], 0)).toBe('full')
  })

  it('runs the suite a changed test file belongs to', () => {
    expect(planFor(['apps/web/tests/TaskProgressStore.test.ts'], 1)).toBe('changed')
  })

  it('skips the suite for prose under apps/web', () => {
    expect(planFor(['apps/web/README.md'], 0)).toBe('skip')
  })

  it('skips the suite when nothing under apps/web changed', () => {
    expect(planFor([], 0)).toBe('skip')
  })

  it('runs everything when a mixed commit hides unmapped source', () => {
    expect(planFor(['docs/notes.md', 'apps/web/src/new/widget.ts'], 0)).toBe('full')
  })

  it('keeps only the paths under apps/web from a git diff listing', () => {
    expect(webFilesUnder('apps/web/src/a.ts\0docs/x.md\0apps/web/tests/a.test.ts\0')).toEqual([
      'apps/web/src/a.ts',
      'apps/web/tests/a.test.ts',
    ])
  })

  it('treats the vitest config as suite code, not as prose', () => {
    expect(planFor(['apps/web/vitest.config.ts'], 0)).toBe('full')
  })
})

describe('the web launcher as a command', () => {
  const deps = (changed, suites, status = 0) => {
    const runs = []
    const lists = []
    return {
      runs,
      lists,
      changed: () => changed,
      list: (reportFile) => {
        lists.push(reportFile)
        return suites
      },
      run: (args) => {
        runs.push(args)
        return { status }
      },
    }
  }

  it('explains the plan and runs nothing', async () => {
    const seam = deps(['apps/web/src/stores/taskProgress.ts'], 2)

    const code = await main(['--explain'], seam)

    expect(code).toBe(0)
    expect(seam.runs).toEqual([])
    expect(seam.lists).toHaveLength(1)
  })

  it('explains a whole-suite decision and still runs nothing', async () => {
    const seam = deps(['apps/web/src/new/widget.ts'], 0)

    const code = await main(['--explain'], seam)

    expect(code).toBe(0)
    expect(seam.runs).toEqual([])
  })

  it('runs the related suites in one command when the flag is absent', async () => {
    const seam = deps(['apps/web/src/stores/taskProgress.ts'], 2)

    const code = await main([], seam)

    expect(code).toBe(0)
    expect(seam.runs).toEqual([['run', '--changed']])
  })

  it('runs the whole suite when changed source reaches no suite', async () => {
    const seam = deps(['apps/web/src/new/widget.ts'], 0)

    const code = await main([], seam)

    expect(code).toBe(0)
    expect(seam.runs).toEqual([['run']])
  })

  it('runs nothing for a commit the suite cannot observe', async () => {
    const seam = deps(['apps/web/README.md'], 0)

    const code = await main([], seam)

    expect(code).toBe(0)
    expect(seam.runs).toEqual([])
  })

  it('fails the gate when git cannot be read', async () => {
    const code = await main([], {
      changed: () => {
        throw new Error('git diff failed: no index')
      },
      list: () => 0,
      run: () => ({ status: 0 }),
    })

    expect(code).toBe(2)
  })

  it('fails the gate when vitest collection fails', async () => {
    const seam = deps(['apps/web/src/stores/taskProgress.ts'], 0)
    seam.list = () => {
      throw new Error('vitest list failed')
    }

    const code = await main([], seam)

    expect(code).toBe(1)
    expect(seam.runs).toEqual([])
  })

  it('returns the suite exit code, not a success of its own', async () => {
    const seam = deps(['apps/web/src/stores/taskProgress.ts'], 2, 1)

    const code = await main([], seam)

    expect(code).toBe(1)
  })
})
