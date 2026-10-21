import { test, expect } from '@playwright/test'
import {
  BACKUP_MODEL,
  clearApiRoutes,
  durableEvent,
  mockApi,
  mockDurableApi,
  runReadNaming,
  setAuthToken,
  E2E_PROFILES,
  E2E_RUN_CANCELLED,
  E2E_RUN_COMPLETED,
  E2E_RUN_FAILED,
  E2E_RUN_LOCAL_COMPLETED,
  E2E_RUN_MESSAGES_COMPLETED,
  E2E_RUN_RUNNING,
  LOCAL_MODEL,
} from './fixtures/api'
import type { Page } from '@playwright/test'

/**
 * Provider portability in the browser (plan Task 10): what a turn answers
 * with, that the choice reaches the request rather than only the card, and
 * that a run keeps its own end. Every response is a fixture — no provider is
 * contacted and no fixture names a credential.
 *
 * The journey each test takes is the one a person using this takes: read the
 * catalog, choose, send, watch the stream, and reload to see the same thing.
 */

async function openConversation(page: Page): Promise<void> {
  await setAuthToken(page)
  await page.goto('/')
  await page.getByRole('button', { name: /Deploy pipeline/ }).waitFor()
  await page.getByRole('button', { name: /Open conversation Deploy/ }).click()
  await page.getByTestId('provider-selector').waitFor()
}

async function chooseProfile(page: Page, label: string): Promise<void> {
  const trigger = page.getByTestId('model-trigger')
  if ((await trigger.getAttribute('aria-expanded')) !== 'true') await trigger.click()
  await page.getByTestId('provider-option').filter({ hasText: label }).getByRole('menuitemradio').click()
}

/** The whole turn: choose, send, and let the stream reach its end. */
async function sendATurn(page: Page, content: string): Promise<void> {
  const composer = page.getByLabel('Message')
  await composer.fill(content)
  await composer.press('Enter')
  // The composer is disabled only while a turn is being answered, so an
  // enabled "Send" is the browser's own signal that the turn ended.
  await expect(page.getByRole('button', { name: 'Send' })).toBeVisible()
}

function aTurnRecord(content: string, end: string) {
  return [
    durableEvent(0, 'turn_submitted', { payload: { content } }),
    durableEvent(1, 'run.queued'),
    durableEvent(2, 'run.started'),
    durableEvent(3, end),
  ]
}

test('a profile this installation cannot serve is stated before anything is sent', async ({
  page,
}) => {
  await clearApiRoutes(page)
  await mockApi(page)
  await mockDurableApi(page)
  await openConversation(page)
  await page.getByTestId('model-trigger').click()

  // The reason is stated in the same place the choices are, so a reader acts
  // instead of guessing why a name has no button. Two reasons are shown here,
  // because the reason is what a reader acts on and neither one is hidden
  // behind a name with nothing under it: a key this installation does not
  // configure, and a declaration this build cannot use.
  const missingKey = page.getByTestId('provider-option').filter({ hasText: 'Needs a key' })
  const invalidBinding = page.getByTestId('provider-option').filter({ hasText: 'Needs a binding' })
  await expect(missingKey).toContainText('Not available in this installation')
  await expect(invalidBinding).toContainText('This profile needs a credential binding it does not name')
  expect(await missingKey.getByRole('button').count()).toBe(0)
  expect(await invalidBinding.getByRole('button').count()).toBe(0)
})

test('choosing another profile names it, and the next turn asks for it', async ({
  page,
}) => {
  await clearApiRoutes(page)
  await mockApi(page)
  const asked = await mockDurableApi(page, {
    runReads: [runReadNaming(BACKUP_MODEL)],
    streamEvents: aTurnRecord('Deploy staging', 'run.succeeded'),
  })
  await openConversation(page)

  await chooseProfile(page, 'Backup model')
  // The choice is stated where the next turn is composed, and it is the
  // choice that answers — not the installation's default.
  await expect(page.getByTestId('model-trigger')).toContainText('backup-fixture')

  await sendATurn(page, 'Deploy staging')

  // What the browser sent, read from the request itself rather than inferred
  // from what the card later shows.
  expect(asked.submitted).toHaveLength(1)
  expect(asked.submitted[0].payload['provider_profile_id']).toBe('backup-model')
})

test('a chosen profile survives a reload, because the run read states it', async ({
  page,
}) => {
  await clearApiRoutes(page)
  await mockApi(page)
  const record = aTurnRecord('Deploy staging', 'run.succeeded')
  const asked = await mockDurableApi(page, {
    runReads: [runReadNaming(BACKUP_MODEL)],
    snapshotEvents: record,
    streamEvents: record,
  })
  await openConversation(page)

  await chooseProfile(page, 'Backup model')
  await sendATurn(page, 'Deploy staging')

  const named =
    page.getByText('Backup model · backup-fixture at api.backup.test')
  await page.getByTestId('run-summary').click()
  await expect(named).toBeVisible()

  // A reload is a different browser: the events it replays are pruned and
  // carry no provider, so the card can only name one because the run read
  // said so.
  await page.reload()
  await expect(page).toHaveURL(/\/conversation\/c1$/)
  await page.getByTestId('run-summary').click()
  await expect(named).toBeVisible()
  expect(asked.submitted).toHaveLength(1)
})

test('a destination of a different kind reads the same way', async ({ page }) => {
  await clearApiRoutes(page)
  await mockApi(page)
  const asked = await mockDurableApi(page, {
    runReads: [E2E_RUN_LOCAL_COMPLETED],
    streamEvents: aTurnRecord('Deploy staging', 'run.succeeded'),
  })
  await openConversation(page)

  // A profile at a destination of a different kind, reached the same way as
  // the first: the browser never learns what an adapter is or what a
  // destination is, so nothing here is a second kind of choice — it is the
  // same choice, at another address.
  await chooseProfile(page, 'Local model')
  await sendATurn(page, 'Deploy staging')

  // The request itself names the profile, so the choice reached the server
  // rather than only the card.
  expect(asked.submitted[0].payload['provider_profile_id']).toBe('local-model')
  await page.getByTestId('run-summary').click()

  // The card makes the same sentence it made for the first profile: a label,
  // a model, a destination. Nothing about this one is shaped by either.
  await expect(
    page.getByText('Local model · local-fixture at localhost:11434'),
  ).toBeVisible()

  // Missing usage leaves the compact summary's token statistic absent.
  await expect(page.getByTestId('run-total-tokens')).toHaveCount(0)
})

test('a profile under an adapter added later reads the same way', async ({ page }) => {
  await clearApiRoutes(page)
  await mockApi(page)
  const asked = await mockDurableApi(page, {
    runReads: [E2E_RUN_MESSAGES_COMPLETED],
    streamEvents: aTurnRecord('Deploy staging', 'run.succeeded'),
  })
  await openConversation(page)

  // The same choice, at a destination reached by a second adapter: nothing in
  // the browser was written for one adapter, so a profile added later is
  // offered and chosen the way the first ones were.
  await chooseProfile(page, 'Messages model')
  await sendATurn(page, 'Deploy staging')

  expect(asked.submitted[0].payload['provider_profile_id']).toBe('messages-model')
  await page.getByTestId('run-summary').click()

  // Input/output counts do not imply a provider-reported total.
  await expect(
    page.getByText('Messages model · messages-fixture at api.messages.test'),
  ).toBeVisible()
  await expect(page.getByTestId('run-total-tokens')).toHaveCount(0)
})

test('a fallback is offered only where the chosen profile allows it', async ({
  page,
}) => {
  await clearApiRoutes(page)
  await mockApi(page)
  const asked = await mockDurableApi(page, {
    runReads: [E2E_RUN_COMPLETED],
    streamEvents: aTurnRecord('Deploy staging', 'run.succeeded'),
  })
  await openConversation(page)

  // The default profile names one destination it can fall back to, so the
  // opt-in is offered; the backup profile names none, so it is not.
  await page.getByTestId('model-trigger').click()
  await page.getByTestId('fallback-option').filter({ hasText: 'backup-fixture' }).click()
  await sendATurn(page, 'Deploy staging')
  expect(asked.submitted[0].payload['fallback_profile_ids']).toEqual(['backup-model'])

  // Choosing a profile that allows no fallback drops the promise rather than
  // leaving a control the server would refuse.
  await chooseProfile(page, 'Backup model')
  await page.getByTestId('model-trigger').click()
  expect(await page.getByTestId('fallback-option').count()).toBe(0)
})

test('a bounded outage is reported with the copy the API settled', async ({
  page,
}) => {
  await clearApiRoutes(page)
  await mockApi(page)
  await mockDurableApi(page, {
    runReads: [E2E_RUN_FAILED],
    streamEvents: aTurnRecord('Deploy staging', 'run.failed'),
  })
  await openConversation(page)
  await sendATurn(page, 'Deploy staging')

  await expect(page.getByText('Failed')).toBeVisible()
  await page.getByTestId('run-summary').click()
  // The safe copy, never a provider's own body: the API settled these words.
  await expect(
    page.getByText('The model provider is busy right now. Try again in a moment.'),
  ).toBeVisible()
  // A turn that did not complete leaves no answer behind it.
  expect(await page.getByText('Answered by the fixture.').count()).toBe(0)
})

test('a cancellation the record carries leaves its own end', async ({ page }) => {
  await clearApiRoutes(page)
  await mockApi(page)
  await mockDurableApi(page, {
    runReads: [E2E_RUN_CANCELLED],
    streamEvents: aTurnRecord('Deploy staging', 'run_cancelled'),
  })
  await openConversation(page)
  await sendATurn(page, 'Deploy staging')

  // Nothing here stopped the turn from the browser: this is an end someone
  // else reached, and the browser only has to agree with the record. The
  // card says cancelled, and a turn with no answer still has none.
  await expect(page.getByText('Cancelled')).toBeVisible()
  expect(await page.getByText('Answered by the fixture.').count()).toBe(0)
})

test('a run that is still going can be stopped from the browser', async ({ page }) => {
  await clearApiRoutes(page)
  await mockApi(page)
  const asked = await mockDurableApi(page, {
    // The record ends while the run is still going, and the first run read
    // agrees: nothing has answered. The second read is what the cancellation
    // leaves, and the browser learns the end from it rather than assuming
    // that asking was enough.
    runReads: [E2E_RUN_RUNNING, E2E_RUN_CANCELLED],
    streamEvents: [
      durableEvent(0, 'turn_submitted', { payload: { content: 'Deploy staging' } }),
      durableEvent(1, 'run.queued'),
      durableEvent(2, 'run.started'),
    ],
  })
  await openConversation(page)
  await sendATurn(page, 'Deploy staging')

  // The control appears beside a run that can still be stopped, and only
  // there: a run that reached an end has nothing left to stop, and the
  // scenario above is that one.
  const cancel = page.getByRole('button', { name: 'Cancel run' })
  await expect(cancel).toBeVisible()
  await cancel.click()

  await expect(page.getByText('Cancelled')).toBeVisible()

  // The browser asked about this run rather than another one, and asked at
  // all — a card that changed on its own would be a lie.
  expect(asked.cancelled).toEqual(['run-e2e-1'])

  // A turn with no answer leaves none, even after a person stops it.
  expect(await page.getByText('Answered by the fixture.').count()).toBe(0)
})

test('a completed turn leaves exactly one answer', async ({ page }) => {
  await clearApiRoutes(page)
  await mockApi(page)
  const asked = await mockDurableApi(page, {
    runReads: [E2E_RUN_COMPLETED],
    streamEvents: aTurnRecord('Deploy staging', 'run.succeeded'),
  })
  await openConversation(page)
  await sendATurn(page, 'Deploy staging')

  // The journey is only meaningful if the record was actually asked: a URL
  // pattern that misses the stream's query leaves the browser on a 404, and
  // every assertion below would still hold.
  expect(asked.stream).toHaveLength(1)
  // One answer, from the messages endpoint — the stream never invents one.
  await expect(page.getByText('Answered by the fixture.')).toBeVisible()
  expect(await page.getByText('Answered by the fixture.').count()).toBe(1)
})
