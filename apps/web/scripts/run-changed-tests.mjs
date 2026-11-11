#!/usr/bin/env node
// Run the web tests a commit can affect, chosen the way the API launcher
// chooses its lane: related suites only when the suite sees a relation, the
// whole suite when changed code relates to nothing the suite can run, and
// nothing at all only for a commit the suite cannot observe. The decision and
// the command are printed, so a narrowed run is auditable afterwards, and
// --explain announces both without running anything.
import { spawnSync } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

export const WEB_SRC_PREFIX = 'apps/web/src/'

// Changes to these cannot be judged by a narrowed run: the suite's own
// configuration decides what the suite is.
const SUITE_CONFIG = new Set([
  'apps/web/vitest.config.ts',
  'apps/web/vite.config.js',
  'apps/web/tsconfig.json',
  'apps/web/package.json',
  'apps/web/pnpm-lock.yaml',
  'apps/web/pnpm-workspace.yaml',
])

const webRoot = () => fileURLToPath(new URL('..', import.meta.url))

export function planFor(changedWebFiles, relatedSuites) {
  const code = changedWebFiles.filter((path) => !path.endsWith('.md'))
  if (code.length === 0) {
    return 'skip'
  }
  const suiteConfig = code.filter((path) => SUITE_CONFIG.has(path))
  if (suiteConfig.length > 0) {
    return 'full'
  }
  const source = code.filter((path) => path.startsWith(WEB_SRC_PREFIX))
  if (source.length > 0 && relatedSuites === 0) {
    return 'full'
  }
  return 'changed'
}

export function webFilesUnder(nulSeparatedPaths) {
  return nulSeparatedPaths.split('\0').filter((path) => path.startsWith('apps/web/'))
}

function gitChangedWebFiles() {
  const result = spawnSync('git', ['diff', '--name-only', '-z', 'HEAD'], {
    cwd: webRoot(),
    encoding: 'utf8',
  })
  if (result.error || result.status !== 0) {
    throw new Error(`git diff failed: ${(result.stderr || result.error?.message || '').trim()}`)
  }
  return webFilesUnder(result.stdout)
}

export function listRelatedSuites(reportFile) {
  // Collection only. `vitest list` never executes a test, so the
  // "related suites vs whole suite" decision costs no run - and an explanation
  // can be honest about the plan it is announcing.
  const argv = ['exec', 'vitest', 'list', '--changed', '--filesOnly', `--json=${reportFile}`]
  const result = spawnSync('pnpm', argv, { cwd: webRoot(), encoding: 'utf8' })
  if (result.error || result.status !== 0) {
    throw new Error(`vitest list failed: ${(result.stderr || result.error?.message || '').trim()}`)
  }
  return JSON.parse(readFileSync(reportFile, 'utf8')).length
}

function runVitest(args) {
  const argv = ['exec', 'vitest', ...args]
  console.log(`pnpm ${argv.join(' ')}`)
  return spawnSync('pnpm', argv, { cwd: webRoot(), stdio: 'inherit' })
}

function commandFor(plan) {
  if (plan === 'full') {
    return 'pnpm exec vitest run'
  }
  if (plan === 'changed') {
    return 'pnpm exec vitest run --changed'
  }
  return 'none'
}

/**
 * @param {string[]} [argv]
 * @param {{ changed?: () => string[], list?: (reportFile: string) => number,
 *   run?: (args: string[]) => { status: number | null } }} [deps]
 */
export async function main(argv = [], deps = {}) {
  const changed = deps.changed ?? gitChangedWebFiles
  const list = deps.list ?? listRelatedSuites
  const run = deps.run ?? runVitest
  const explain = argv.includes('--explain')

  let files
  try {
    files = changed()
  } catch (error) {
    console.log(`git failed: ${error.message}`)
    return 2
  }

  const scratch = mkdtempSync(join(tmpdir(), 'openship-web-changed-'))
  const reportFile = join(scratch, 'report.json')
  let suites = 0
  try {
    if (files.length > 0) {
      try {
        suites = list(reportFile)
      } catch (error) {
        console.log(`vitest collection failed: ${error.message}`)
        return 1
      }
    }
  } finally {
    rmSync(scratch, { recursive: true, force: true })
  }

  const plan = planFor(files, suites)
  console.log(`web test selection: ${plan}`)
  if (explain) {
    console.log(`  changed under apps/web: ${files.join(', ') || 'none'}`)
    console.log(`  suites vitest reaches: ${suites}`)
    console.log(`  command: ${commandFor(plan)}`)
    // --explain is the inspection mode: the decision and the exact command,
    // and nothing executed. The commit hook runs this script without the flag.
    return 0
  }

  if (plan === 'skip') {
    console.log('skip: no file the web suite observes')
    return 0
  }
  if (plan === 'full') {
    console.log('full: the change cannot be judged by a narrowed run; running the whole suite')
    const full = run(['run'])
    return full.status ?? 1
  }
  const narrowed = run(['run', '--changed'])
  return narrowed.status ?? 1
}

const invokedDirectly =
  import.meta.url.startsWith('file://') && process.argv[1] === fileURLToPath(import.meta.url)

if (invokedDirectly) {
  main(process.argv.slice(2)).then((code) => process.exit(code))
}
