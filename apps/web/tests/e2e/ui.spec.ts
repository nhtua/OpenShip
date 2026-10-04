import { test, expect, type Page } from '@playwright/test'
import {
  clearApiRoutes,
  makeConversations,
  makeMessages,
  mockApi,
  setAuthToken,
} from './fixtures/api'

/**
 * Real computed-style, responsive, and asset verification (plan Tasks 7.3–7.5).
 * These assert getComputedStyle and bounding boxes against the visual
 * contract, so they fail if the CSS reverts to a v3 entry point or loses the
 * semantic tokens.
 */

const VIEWS = [
  { name: 'desktop-wide', width: 1440, height: 900 },
  { name: 'laptop', width: 1024, height: 768 },
  { name: 'tablet-landscape', width: 768, height: 1024 },
  { name: 'breakpoint-edge', width: 767, height: 1024 },
  { name: 'mobile', width: 390, height: 844 },
]

async function noPageOverflow(page: Page): Promise<void> {
  const { scrollWidth, clientWidth } = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }))
  expect(
    scrollWidth,
    `page-level horizontal overflow (${scrollWidth} > ${clientWidth})`,
  ).toBeLessThanOrEqual(clientWidth + 1)
}

test.describe('visual contract: auth (7.3)', () => {
  test.beforeEach(async ({ page }) => {
    await clearApiRoutes(page)
    await mockApi(page)
  })

  test('login submit button has 36px+ height, padding, semantic background, radius, 14px type', async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/login')
    const button = page.getByRole('button', { name: 'Sign In' })
    const metrics = await button.evaluate((el) => {
      const s = getComputedStyle(el)
      return {
        height: el.getBoundingClientRect().height,
        paddingLeft: s.paddingLeft,
        paddingRight: s.paddingRight,
        background: s.backgroundColor,
        borderRadius: s.borderRadius,
        fontSize: s.fontSize,
      }
    })
    expect(metrics.height).toBeGreaterThanOrEqual(36)
    expect(parseFloat(metrics.paddingLeft)).toBeGreaterThan(0)
    expect(parseFloat(metrics.paddingRight)).toBeGreaterThan(0)
    expect(metrics.background).not.toBe('transparent')
    // Nonzero alpha: a v3-less token background must be opaque enough to read.
    expect(metrics.background).not.toMatch(/rgba?\([^)]*,\s*0(\.0+)?\)\s*$/)
    expect(metrics.borderRadius).not.toBe('0px')
    expect(metrics.fontSize).toBe('14px')
  })

  test('auth card padding is 32px desktop / 24px mobile; body margin is 0 (Preflight)', async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/login')
    const card = page.locator('div.max-w-md')
    expect(await card.evaluate((el) => getComputedStyle(el).paddingLeft)).toBe(
      '32px',
    )
    expect(await page.evaluate(() => getComputedStyle(document.body).margin)).toBe(
      '0px',
    )

    await page.setViewportSize({ width: 390, height: 844 })
    expect(await card.evaluate((el) => getComputedStyle(el).paddingLeft)).toBe(
      '24px',
    )
  })
})

test.describe('visual contract: chat shell (7.3)', () => {
  test.beforeEach(async ({ page }) => {
    await clearApiRoutes(page)
    await mockApi(page)
    await setAuthToken(page)
  })

  test('sidebar is 256px; rows have 12px/8px padding with 12px gap; stream padding 24px (desktop)', async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/')
    const row = page.getByRole('button', { name: /Deploy pipeline/ })
    await row.waitFor()

    const sidebarBox = await page
      .locator('[data-slot="sidebar"]')
      .first()
      .boundingBox()
    expect(sidebarBox?.width).toBe(256)

    const rowMetrics = await row.evaluate((el) => {
      const s = getComputedStyle(el)
      return {
        paddingLeft: s.paddingLeft,
        paddingTop: s.paddingTop,
        gap: s.gap,
      }
    })
    expect(rowMetrics.paddingLeft).toBe('12px')
    expect(rowMetrics.paddingTop).toBe('8px')
    expect(rowMetrics.gap).toBe('12px')

    expect(
      await page
        .locator('[data-testid="stream"]')
        .evaluate((el) => getComputedStyle(el).paddingLeft),
    ).toBe('24px')
  })

  test('stream padding is 16px on mobile', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto('/')
    await page.locator('[data-testid="stream"]').waitFor()
    expect(
      await page
        .locator('[data-testid="stream"]')
        .evaluate((el) => getComputedStyle(el).paddingLeft),
    ).toBe('16px')
  })
})

test.describe('responsive layout (7.4)', () => {
  for (const view of VIEWS) {
    test(`no horizontal overflow and visible composer at ${view.name} (${view.width}x${view.height})`, async ({
      page,
    }) => {
      await clearApiRoutes(page)
      await mockApi(page)
      await setAuthToken(page)
      await page.setViewportSize({ width: view.width, height: view.height })
      await page.goto('/')
      await page.getByLabel('Message').waitFor()
      await noPageOverflow(page)

      const composerBox = await page
        .locator('[data-testid="composer"]')
        .boundingBox()
      expect(composerBox, 'composer should be visible').toBeTruthy()
      expect(composerBox.x).toBeGreaterThanOrEqual(0)
      expect(composerBox.x + composerBox.width).toBeLessThanOrEqual(view.width + 1)
      expect(composerBox.y + composerBox.height).toBeLessThanOrEqual(view.height + 1)
    })
  }

  test('sidebar and stream scroll independently with 50 conversations and 100 messages', async ({
    page,
  }) => {
    await clearApiRoutes(page)
    await mockApi(page, {
      conversations: makeConversations(50),
      messages: { 'conv-1': makeMessages(100) },
    })
    await setAuthToken(page)
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/')
    await page.getByRole('button', { name: /Conversation 50/ }).waitFor()

    // Open the long-title conversation to load 100 messages.
    // Scroll sidebar into view then click (force: true avoids stability waits
    // on 300-char accessible names that time out).
    const longRow = page
      .getByRole('button', { name: /^Open conversation A{200,}$/ })
    await longRow.scrollIntoViewIfNeeded()
    await longRow.click({ force: true })
    // The last message is a 2000-char unbroken 'B' string.
    await page.getByText('B'.repeat(2000)).waitFor()

    const sidebarContent = page.locator('[data-slot="sidebar-content"]')
    const stream = page.locator('[data-testid="stream"]')
    const state = (locator: ReturnType<typeof page.locator>) =>
      locator.evaluate((el) => ({
        scrollHeight: el.scrollHeight,
        clientHeight: el.clientHeight,
        scrollTop: el.scrollTop,
      }))

    const sidebarBefore = await state(sidebarContent)
    const streamBefore = await state(stream)
    expect(
      sidebarBefore.scrollHeight,
      'sidebar must scroll with 50 conversations',
    ).toBeGreaterThan(sidebarBefore.clientHeight)
    expect(
      streamBefore.scrollHeight,
      'stream must scroll with 100 messages',
    ).toBeGreaterThan(streamBefore.clientHeight)

    await sidebarContent.evaluate((el) => {
      el.scrollTop = 120
    })
    await stream.evaluate((el) => {
      el.scrollTop = 400
    })
    const [sidebarAfter, streamAfter] = await Promise.all([
      state(sidebarContent),
      state(stream),
    ])
    expect(sidebarAfter.scrollTop).toBe(120)
    expect(streamAfter.scrollTop).toBe(400)

    // The composer stays pinned and visible while both regions scroll.
    const composerBox = await page
      .locator('[data-testid="composer"]')
      .boundingBox()
    expect(composerBox, 'composer must stay visible').toBeTruthy()
    expect(composerBox.y + composerBox.height).toBeLessThanOrEqual(900 + 1)
  })

  test('300-char title and 2000-char unbroken message do not overflow the page', async ({
    page,
  }) => {
    await clearApiRoutes(page)
    await mockApi(page, {
      conversations: makeConversations(3),
      messages: { 'conv-1': makeMessages(3) },
    })
    await setAuthToken(page)
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/')

    // Scroll sidebar into view then click the long-title row (force: true
    // avoids stability waits that time out on 300-char accessible names).
    const longRow = page
      .getByRole('button', { name: /^Open conversation A{200,}$/ })
    await longRow.scrollIntoViewIfNeeded()
    await longRow.click({ force: true })

    // The last message is a 2000-char unbroken 'B' string.
    await page.getByText('B'.repeat(2000)).waitFor()
    await noPageOverflow(page)

    // The unbroken 2000-char message wraps instead of widening the bubble.
    const metrics = await page
      .locator('[data-testid="stream"] p')
      .last()
      .evaluate((el) => ({
        scrollWidth: el.scrollWidth,
        clientWidth: el.clientWidth,
      }))
    expect(metrics.scrollWidth).toBeLessThanOrEqual(metrics.clientWidth + 1)

    await page.setViewportSize({ width: 390, height: 844 })
    await noPageOverflow(page)
  })

  test('desktop sidebar collapses to icon-only and reopens from the footer control', async ({
    page,
  }) => {
    await clearApiRoutes(page)
    await mockApi(page)
    await setAuthToken(page)
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/')
    await page.getByRole('button', { name: /Deploy pipeline/ }).waitFor()

    await page.getByRole('button', { name: 'Toggle Sidebar' }).click()
    // The sidebar animates (200ms); poll the settled width instead of
    // measuring mid-transition.
    await expect.poll(async () => {
      const box = await page.locator('[data-slot="sidebar"]').first().boundingBox()
      return box?.width
    }).toBe(48)

    const expand = page.getByRole('button', { name: 'Expand sidebar' })
    await expect(expand).toBeVisible()
    await expand.click()
    await expect.poll(async () => {
      const box = await page.locator('[data-slot="sidebar"]').first().boundingBox()
      return box?.width
    }).toBe(256)
  })

  test('mobile drawer opens, closes on Escape, and returns focus to the trigger', async ({
    page,
  }) => {
    await clearApiRoutes(page)
    await mockApi(page)
    await setAuthToken(page)
    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto('/')
    await page.locator('[data-testid="stream"]').waitFor()

    const trigger = page.getByRole('button', { name: 'Toggle Sidebar' })
    await trigger.click()
    const drawer = page.locator('[data-mobile="true"]')
    await expect(drawer).toBeVisible()

    await page.keyboard.press('Escape')
    await expect(drawer).toBeHidden()

    const activeAriaLabel = await page.evaluate(
      () => document.activeElement?.getAttribute('aria-label') ?? null,
    )
    expect(activeAriaLabel).toBe('Toggle Sidebar')
  })

  test('keyboard activation loads a conversation row', async ({ page }) => {
    await clearApiRoutes(page)
    await mockApi(page)
    await setAuthToken(page)
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/')
    await page.getByRole('button', { name: /Scale up web/ }).waitFor()

    const row = page.getByRole('button', { name: /Scale up web/ })
    await row.focus()
    await page.keyboard.press('Enter')
    await page.getByText('Scale web to 3 replicas').waitFor()
  })

  test('composer is addressable by label and shows a visible focus ring', async ({
    page,
  }) => {
    await clearApiRoutes(page)
    await mockApi(page)
    await setAuthToken(page)
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/')

    const textarea = page.getByLabel('Message')
    await textarea.waitFor()
    await textarea.focus()
    const ring = await textarea.evaluate((el) => {
      const s = getComputedStyle(el)
      return {
        boxShadow: s.boxShadow,
        outlineWidth: s.outlineWidth,
        outlineStyle: s.outlineStyle,
      }
    })
    const hasRing =
      ring.boxShadow !== 'none' ||
      (ring.outlineStyle !== 'none' && ring.outlineWidth !== '0px')
    expect(hasRing, 'focused composer must show a visible ring').toBe(true)
  })
})

test.describe('theme and icons (7.5)', () => {
  test('dark is the default; toggling to light persists across refresh', async ({
    page,
  }) => {
    await clearApiRoutes(page)
    await mockApi(page)
    await setAuthToken(page)
    await page.goto('/')
    expect(
      await page.evaluate(() => document.documentElement.classList.contains('dark')),
    ).toBe(true)
    expect(await page.evaluate(() => localStorage.getItem('openship-theme'))).toBeNull()

    await page.getByRole('button', { name: 'Switch to light mode' }).click()
    expect(
      await page.evaluate(() => document.documentElement.classList.contains('dark')),
    ).toBe(false)
    expect(await page.evaluate(() => localStorage.getItem('openship-theme'))).toBe(
      'light',
    )

    await page.reload()
    expect(
      await page.evaluate(() => document.documentElement.classList.contains('dark')),
    ).toBe(false)
  })

  test('PrimeIcons font loads and a used icon ::before has real content', async ({
    page,
  }) => {
    await clearApiRoutes(page)
    await mockApi(page)
    await page.goto('/login')
    // Poll the actual load state: document.fonts.ready can resolve before
    // the icon font fetch is even initiated on first paint.
    await page.waitForFunction(() => document.fonts.check('16px "primeicons"'))

    expect(await page.evaluate(() => document.fonts.check('16px "primeicons"'))).toBe(
      true,
    )
    const before = await page.locator('.pi-user').first().evaluate((el) => {
      const s = getComputedStyle(el, '::before')
      return { content: s.content, fontFamily: s.fontFamily }
    })
    expect(before.content).not.toBe('none')
    expect(before.content).not.toBe('"none"')
    expect(before.fontFamily.toLowerCase()).toContain('primeicon')
  })

  test('native controls follow the theme color scheme', async ({ page }) => {
    await clearApiRoutes(page)
    await mockApi(page)
    await setAuthToken(page)
    await page.goto('/')
    const colorScheme = await page.evaluate(
      () => getComputedStyle(document.documentElement).colorScheme,
    )
    expect(colorScheme).toContain('dark')
  })

  test('no failed CSS/font requests and no application console errors', async ({
    page,
  }) => {
    const failedAssets: string[] = []
    const consoleErrors: string[] = []
    page.on('response', (res) => {
      if (res.status() >= 400 && /\.(css|woff2?|ttf|eot)(\?|$)/i.test(res.url())) {
        failedAssets.push(res.url())
      }
    })
    page.on('console', (msg) => {
      if (msg.type() === 'error') consoleErrors.push(msg.text())
    })
    page.on('pageerror', (err) => consoleErrors.push(err.message))

    await clearApiRoutes(page)
    await mockApi(page)
    await setAuthToken(page)
    await page.goto('/')
    await page.getByRole('button', { name: /Deploy pipeline/ }).waitFor()

    expect(failedAssets).toEqual([])
    // The browser's automatic /favicon.ico probe is not an application error.
    expect(consoleErrors.filter((e) => !/favicon/i.test(e))).toEqual([])
  })
})
