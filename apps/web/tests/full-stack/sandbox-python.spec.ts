import { expect, test } from '@playwright/test'

test('lets a chat turn download a CSV, chart it with sandbox Python, and preview the PNG', async ({ page }) => {
  const suffix = `${Date.now()}${Math.floor(Math.random() * 10000)}`
  await page.goto('/register')
  await page.getByLabel('Username').fill(`sandboxpython${suffix}`)
  await page.getByLabel('Email').fill(`sandboxpython${suffix}@example.test`)
  await page.getByRole('textbox', { name: 'Password', exact: true }).fill('synthetic-test-password')
  await page.getByRole('textbox', { name: 'Confirm Password' }).fill('synthetic-test-password')
  await page.getByRole('button', { name: 'Create Account' }).click()

  await page.waitForURL(/\/$/)
  await expect(page.getByTestId('composer')).toBeVisible()

  await page.getByRole('button', { name: 'Add tool' }).click()
  const automaticTools = page.getByTestId('automatic-tool-mode')
  await expect(automaticTools).toBeEnabled()
  await expect(automaticTools).toHaveAttribute('aria-checked', 'true')

  await page.getByLabel('Message').fill('Chart the CSV at https://data.example.com/reports/sales.csv as a bar chart.')
  await page.getByRole('button', { name: 'Send', exact: true }).click()

  const downloadCard = page.getByTestId('tool-operation-card').first()
  await expect(downloadCard).toContainText(/Download|Fetch/i)
  await expect(downloadCard).toContainText('Completed', { timeout: 120_000 })

  const card = page.getByTestId('python-operation').last()
  await expect(card).toBeVisible()
  await expect(card.getByTestId('python-operation-status')).toContainText('Completed', { timeout: 180_000 })
  await expect(card.getByTestId('python-stdout')).toContainText('4 rows charted')

  await expect(card.getByTestId('chart-preview')).toBeVisible()
  await expect(card.getByTestId('chart-preview')).toHaveAttribute('alt', 'chart.png preview')
  await expect(card.getByTestId('preview-unavailable')).toHaveCount(0)
  await expect(card.getByRole('button', { name: 'Download chart.png' })).toBeEnabled()

  // Durable continuation replays the recorded steps without new provider work.
  const resumeButton = card.getByTestId('resume-last-step')
  await expect(resumeButton).toBeEnabled()
  await resumeButton.click()
  await expect(card.getByTestId('python-operation-status')).toContainText('Completed', { timeout: 120_000 })
  await expect(card.getByTestId('chart-preview')).toBeVisible()
})
