import { expect, test } from '@playwright/test'
import { mockApi, setAuthToken } from './fixtures/api'

test.beforeEach(async ({ page }) => {
  await mockApi(page)
})

test('a bookmarked conversation survives refresh and browser history navigation', async ({ page }) => {
  await setAuthToken(page)
  await page.goto('/conversation/c2')
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Scale up web')
  await expect(page.getByText('Scaled web to 3 replicas.', { exact: true })).toBeVisible()

  await page.reload()
  await expect(page.getByText('Scaled web to 3 replicas.', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: /Open conversation Deploy pipeline/ }).click()
  await expect(page).toHaveURL(/\/conversation\/c1$/)
  await expect(page.getByText('Staging deployed successfully.', { exact: true })).toBeVisible()

  await page.goBack()
  await expect(page).toHaveURL(/\/conversation\/c2$/)
  await expect(page.getByText('Scaled web to 3 replicas.', { exact: true })).toBeVisible()
  await page.goForward()
  await expect(page.getByText('Staging deployed successfully.', { exact: true })).toBeVisible()

  await page.getByRole('link', { name: 'Agent Workspace' }).click()
  await expect(page).toHaveURL(/\/$/)
  await expect(page.getByTestId('empty-state')).toBeVisible()
  await expect(page.getByText('Staging deployed successfully.', { exact: true })).toHaveCount(0)
})

test('signing in returns to the bookmarked conversation', async ({ page }) => {
  await page.goto('/conversation/c1')
  await expect(page).toHaveURL(/\/login\?redirect=/)
  await page.getByLabel('Username').fill('testuser')
  await page.getByLabel('Password').fill('password123')
  await page.getByRole('button', { name: 'Sign In' }).click()

  await expect(page).toHaveURL(/\/conversation\/c1$/)
  await expect(page.getByText('Staging deployed successfully.', { exact: true })).toBeVisible()
})

test('a missing conversation shows an error at its own URL', async ({ page }) => {
  await setAuthToken(page)
  await page.route('**/api/conversations/missing/messages', (route) => route.fulfill({
    status: 404,
    contentType: 'application/json',
    body: JSON.stringify({ detail: 'Conversation not found' }),
  }))
  await page.goto('/conversation/missing')

  await expect(page.getByRole('alert')).toContainText('Conversation not found')
  await expect(page).toHaveURL(/\/conversation\/missing$/)
  await expect(page.getByText('Staging deployed successfully.', { exact: true })).toHaveCount(0)
})

test('the first message creates a conversation URL and keeps its response there', async ({ page }) => {
  await setAuthToken(page)
  await page.goto('/')
  await page.getByRole('textbox', { name: 'Message', exact: true }).fill('New conversation request')
  await page.getByRole('button', { name: 'Send', exact: true }).click()

  await expect(page).toHaveURL(/\/conversation\/c-new$/)
  await expect(page.getByText('New conversation request', { exact: true })).toBeVisible()
  await expect(page.getByText('Hello world', { exact: true })).toBeVisible()
  await page.goBack()
  await expect(page).toHaveURL(/\/$/)
  await expect(page.getByTestId('empty-state')).toBeVisible()
})

test('an encoded path in a conversation ID cannot load another conversation', async ({ page }) => {
  await setAuthToken(page)
  await page.goto('/conversation/c1%2F..%2Fc2')

  await expect(page.getByRole('alert')).toContainText('Invalid conversation link')
  await expect(page.getByText('Scaled web to 3 replicas.', { exact: true })).toHaveCount(0)
})
