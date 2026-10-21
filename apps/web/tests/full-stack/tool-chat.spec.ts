import { expect, test } from '@playwright/test'

test('lets the selected model choose an enabled search tool in chat', async ({ page }) => {
  const suffix = `${Date.now()}${Math.floor(Math.random() * 10000)}`
  await page.goto('/register')
  await page.getByLabel('Username').fill(`toolchat${suffix}`)
  await page.getByLabel('Email').fill(`toolchat${suffix}@example.test`)
  await page.getByRole('textbox', { name: 'Password', exact: true }).fill('synthetic-test-password')
  await page.getByRole('textbox', { name: 'Confirm Password' }).fill('synthetic-test-password')
  await page.getByRole('button', { name: 'Create Account' }).click()

  await page.waitForURL(/\/$/)
  await expect(page.getByTestId('composer')).toBeVisible()
  await expect(page.getByTestId('model-trigger')).toContainText('gpt-6-luna')

  await page.getByRole('button', { name: 'Add tool' }).click()
  const automaticTools = page.getByTestId('automatic-tool-mode')
  await expect(automaticTools).toBeEnabled()
  await expect(automaticTools).toHaveAttribute('aria-checked', 'true')
  await expect(page.getByTestId('attached-tool')).toHaveCount(0)

  await page.getByLabel('Message').fill('Search the web for the nearest star system to the Sun.')
  await page.getByRole('button', { name: 'Send', exact: true }).click()

  const card = page.getByTestId('tool-operation-card').last()
  await expect(card).toContainText(/Brave Search|Tavily Search/)
  await expect(card).toContainText('Completed', { timeout: 120_000 })
  await expect(card).toContainText(/nearest star system/i)
  await page.getByRole('button', { name: /Show results for .*nearest star system/i }).click()
  await expect(card.getByRole('link')).toHaveAttribute('href', /^https?:\/\//)
  await expect(page.getByTestId('attached-tool')).toHaveCount(0)
  await expect(page.getByText(/Alpha Centauri|Proxima Centauri/i).last()).toBeVisible({ timeout: 120_000 })
})
