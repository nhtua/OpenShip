// @vitest-environment node
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, it, expect } from 'vitest'
import { build } from 'vite'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const appsWebRoot = path.resolve(__dirname, '..')

function collectCss(result: Awaited<ReturnType<typeof build>>): string {
  const assets = result.output
  return assets
    .filter(
      (a): a is { fileName: string; source: string; type: 'asset' } =>
        a.type === 'asset' && typeof a.source === 'string' && a.fileName.endsWith('.css')
    )
    .map((a) => a.source)
    .join('\n')
}

describe('Tailwind v4 CSS generation', () => {
  it('generates default theme utilities', async () => {
    const result = await build({
      root: appsWebRoot,
      configFile: path.resolve(appsWebRoot, 'vite.config.js'),
      build: { write: false, minify: false, cssMinify: false },
      logLevel: 'error',
    })

    const css = collectCss(result)

    // Spacing variable from Tailwind v4 default theme
    expect(css).toContain('--spacing:')
    // Standard utilities that should be generated
    for (const selector of ['p-4', 'px-4', 'gap-3', 'text-sm', 'rounded-lg']) {
      expect(css).toMatch(new RegExp(`\\.${selector}\\s*\\{`))
    }
    expect(css).toMatch(/@layer base\s*\{/)
  }, 60_000)

  it('generates semantic root color utilities', async () => {
    const result = await build({
      root: appsWebRoot,
      configFile: path.resolve(appsWebRoot, 'vite.config.js'),
      build: { write: false, minify: false, cssMinify: false },
      logLevel: 'error',
    })

    const css = collectCss(result)

    // Semantic color classes generated via @theme inline { --color-* }
    expect(css).toMatch(/\.bg-background\s*\{[^}]*background-color/)
    expect(css).toMatch(/\.text-foreground\s*\{[^}]*color/)
    expect(css).toMatch(/\.bg-primary\s*\{[^}]*background-color/)
    expect(css).toMatch(/\.text-primary\s*\{[^}]*color/)
  }, 60_000)
})
