import { expect, test } from '@playwright/test'
import { readFile } from 'node:fs/promises'

test('registers, runs each configured search tool, and downloads retained bytes', async ({ page }) => {
  const suffix = `${Date.now()}${Math.floor(Math.random() * 10000)}`
  await page.goto('/register')
  await page.getByLabel('Username').fill(`phase4${suffix}`)
  await page.getByLabel('Email').fill(`phase4${suffix}@example.test`)
  await page.getByRole('textbox', { name: 'Password', exact: true }).fill('synthetic-test-password')
  await page.getByRole('textbox', { name: 'Confirm Password' }).fill('synthetic-test-password')
  await page.getByRole('button', { name: 'Create Account' }).click()

  await page.waitForURL(/\/$/)
  await expect(page.getByTestId('composer')).toBeVisible()

  for (const [toolId, label] of [
    ['brave_web_search', 'Brave Search'],
    ['tavily_web_search', 'Tavily Search'],
  ]) {
    await page.getByRole('button', { name: 'Add tool' }).click()
    await page.getByTestId('tool-choice').filter({ hasText: label }).click()
    await page.getByLabel('Message').fill(`full-stack ${label} verification`)
    await page.getByRole('button', { name: 'Search', exact: true }).click()

    const card = page.getByTestId('tool-operation-card').last()
    await expect(card).toContainText(label)
    await expect(card).toContainText('Completed', { timeout: 60_000 })
    await card.getByRole('button', { name: new RegExp(`Show results for full-stack ${label} verification`) }).click()
    await expect(card.getByRole('link')).toHaveAttribute('href', /^https:\/\//)

    const downloadReady = page.waitForEvent('download')
    await card.getByRole('button', { name: /^Download / }).click()
    const download = await downloadReady
    const path = await download.path()
    expect(path).not.toBeNull()
    const bytes = await readFile(path!)
    expect(bytes.length).toBeGreaterThan(0)
    expect(bytes.toString('utf8')).toContain('full-stack')
  }
})
