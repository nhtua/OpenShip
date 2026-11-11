import { expect, test } from '@playwright/test'

const browserUsername = process.env.OPENSHIP_FULL_STACK_BROWSER_USERNAME
const browserPassword = process.env.OPENSHIP_FULL_STACK_BROWSER_PASSWORD
const completedConversation = process.env.OPENSHIP_FULL_STACK_BROWSER_CONVERSATION
const recoveryConversation = process.env.OPENSHIP_FULL_STACK_BROWSER_RECOVERY_CONVERSATION

const testId = (name: string) => `[data-testid="${name}"]`

test('the browser drives one durable task across a reload and an explicit recovery choice', async ({ page }) => {
  test.skip(
    !browserUsername || !browserPassword || !completedConversation || !recoveryConversation,
    'the agentic task browser lane needs the harness account and its two conversations',
  )

  await page.goto('/login')
  await page.getByLabel('Username').fill(browserUsername!)
  await page.getByLabel('Password').fill(browserPassword!)
  await page.getByRole('button', { name: 'Sign In' }).click()

  await page.waitForURL(/\/$/)
  await expect(page.getByTestId('composer')).toBeVisible()

  // The account's execution mode is its own durable setting: it starts in
  // Copilot, the browser changes it, and a reload keeps it.
  await expect(page.getByTestId('agent-mode-current')).toContainText(/Copilot · revision 1/)
  await expect(page.getByTestId('agent-mode-scope')).toHaveText('Applies to new tasks')
  await page.getByTestId('agent-mode-auto-choice').click()
  await expect(page.getByTestId('agent-mode-current')).toContainText(/Auto · revision 2/)
  await expect(page.locator(testId('agent-mode-pending'))).toHaveCount(0)

  await page.reload()
  await expect(page.getByTestId('agent-mode-current')).toContainText(/Auto · revision 2/)
  await expect(page.getByTestId('composer')).toBeVisible({ timeout: 20_000 })

  // A completed task folds from the durable records: one plan card, the plan
  // that survived the refused envelope, verified steps and recorded outputs.
  await page.goto(`/conversation/${completedConversation}`)
  await expect(page.locator(testId('task-plan'))).toHaveCount(1)
  const planCard = page.locator(testId('task-plan')).first()
  await expect(planCard).toBeVisible()
  await expect(planCard.locator(testId('task-plan-status')).first()).toContainText(/Completed · Copilot/)
  await expect(planCard.locator(testId('task-plan-revision'))).toHaveCount(1)
  await expect(planCard.locator(testId('task-plan-step-fetch')).first()).toContainText(/Verified/)
  await expect(planCard.locator(testId('task-plan-step-chart')).first()).toContainText(/Verified/)
  await expect(planCard.locator(testId('task-plan-output')).filter({ hasText: /averages\.json/ })).toHaveCount(1)
  await expect(planCard.locator(testId('task-plan-output')).filter({ hasText: /chart\.png/ })).toHaveCount(1)
  await expect(planCard.locator(testId('task-plan-skipped'))).toHaveCount(0)
  await expect(planCard.locator(testId('task-plan-completed-banner'))).toHaveCount(1)

  // A recovery park states the diagnosis first and only offers the choices the
  // durable records support: the command that may still be running keeps
  // repeat disabled, and the step that feeds a later step keeps continue
  // disabled, each with the reason the records gave.
  await page.goto(`/conversation/${recoveryConversation}`)
  await expect(page.locator(testId('task-decision'))).toHaveCount(1)
  const decision = page.locator(testId('task-decision')).first()
  await expect(decision).toBeVisible()
  await expect(decision.locator(testId('task-decision-heading')).first()).toContainText(
    /A recovery choice · plan revision 1/,
  )
  await expect(decision.locator(testId('task-decision-diagnosis'))).toHaveCount(1)
  await expect(decision.locator(testId('task-decision-explanation')).first()).toContainText(
    /attempt \d+ of chart/,
  )
  await expect(decision.locator(testId('task-decision-choice'))).toHaveCount(3)
  for (const action of ['abort', 'repeat_step', 'continue_next']) {
    await expect(decision.locator(testId(`task-decision-choice-${action}`))).toHaveCount(1)
  }
  await expect(decision.locator(testId('task-decision-choice-repeat_step')).first()).toBeDisabled()
  const repeatReason = decision.locator(testId('task-decision-reason-repeat_step')).first()
  await expect(repeatReason).toBeVisible()
  await expect(repeatReason).toContainText(/may still be running|already dispatched/)
  await expect(decision.locator(testId('task-decision-choice-continue_next')).first()).toBeDisabled()
  const continueReason = decision.locator(testId('task-decision-reason-continue_next')).first()
  await expect(continueReason).toBeVisible()
  await expect(continueReason).toContainText(/a later step depends/)
  await expect(decision.locator(testId('task-decision-choice-abort')).first()).toBeEnabled()

  // Only the supported choice submits; the others never post a command, and
  // once the run is cancelled the open ask is gone and nothing claims success.
  await page.getByRole('button', { name: 'Abort' }).click()
  await page.goto(`/conversation/${recoveryConversation}`)
  await expect(page.locator(testId('task-decision'))).toHaveCount(0)
  const afterAbort = page.locator(testId('task-plan')).first()
  await expect(afterAbort).toBeVisible()
  await expect(afterAbort.locator(testId('task-plan-status')).first()).toContainText(
    /Cancelled · Copilot/,
  )
  await expect(afterAbort.locator(testId('task-plan-completed-banner'))).toHaveCount(0)
})
