# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: auth-chat.spec.ts >> logout leaves the workspace, clears the token, and lands on login
- Location: tests/e2e/auth-chat.spec.ts:101:5

# Error details

```
Test timeout of 60000ms exceeded.
```

```
Error: locator.click: Test timeout of 60000ms exceeded.
Call log:
  - waiting for getByRole('button', { name: 'testuser' })

```

# Page snapshot

```yaml
- generic [ref=e5]:
  - generic [ref=e8]:
    - generic [ref=e9]:
      - generic [aria-hidden] [ref=e10]: OS
      - generic [ref=e11]: OpenShip
    - generic [ref=e12]:
      - button "New Conversation" [ref=e14]:
        - generic [aria-hidden] [ref=e15]: 
      - navigation "Conversations" [ref=e17]:
        - list [ref=e18]:
          - listitem [ref=e19]:
            - link "Agent Workspace" [ref=e20] [cursor=pointer]:
              - /url: /
              - generic [aria-hidden] [ref=e21]: 
        - list [ref=e24]:
          - listitem [ref=e25]:
            - button "Open conversation Deploy pipeline" [ref=e26]:
              - generic [aria-hidden] [ref=e27]: 
              - generic [ref=e28]: Deploy pipeline
          - listitem [ref=e29]:
            - button "Open conversation Scale up web" [ref=e30]:
              - generic [aria-hidden] [ref=e31]: 
              - generic [ref=e32]: Scale up web
    - generic [ref=e33]:
      - list [ref=e34]:
        - listitem [ref=e35]:
          - button "Workflows" [disabled]:
            - generic [aria-hidden]: 
        - listitem [ref=e36]:
          - button "Tools" [disabled]:
            - generic [aria-hidden]: 
        - listitem [ref=e37]:
          - button "Skills" [disabled]:
            - generic [aria-hidden]: 
        - listitem [ref=e38]:
          - button "Artifacts" [disabled]:
            - generic [aria-hidden]: 
        - listitem [ref=e39]:
          - button "Connectors" [disabled]:
            - generic [aria-hidden]: 
        - listitem [ref=e40]:
          - button "Resources" [disabled]:
            - generic [aria-hidden]: 
        - listitem [ref=e41]:
          - button "Git" [disabled]:
            - generic [aria-hidden]: 
        - listitem [ref=e42]:
          - button "Terraform" [disabled]:
            - generic [aria-hidden]: 
        - listitem [ref=e43]:
          - button "Alarms" [disabled]:
            - generic [aria-hidden]: 
        - listitem [ref=e44]:
          - button "Settings" [disabled]:
            - generic [aria-hidden]: 
      - button [ref=e46]:
        - generic [aria-hidden] [ref=e48]: 
        - generic [aria-hidden] [ref=e49]: 
  - main [ref=e50]:
    - generic [ref=e51]:
      - button "Toggle Sidebar" [ref=e52]:
        - generic [aria-hidden] [ref=e53]: 
      - generic [ref=e55]:
        - heading "New Conversation" [level=1] [ref=e56]
        - status [ref=e57]: Idle
      - button "Switch to light mode" [ref=e60]:
        - generic [aria-hidden] [ref=e61]: 
    - generic [ref=e63]:
      - paragraph [ref=e67]: No messages yet. Start a conversation!
      - generic [ref=e68]:
        - generic [ref=e69]:
          - generic [ref=e70]:
            - generic [ref=e71]: Message
            - textbox "Message" [active] [ref=e72]:
              - /placeholder: Message the agent...
          - button "Send" [disabled]:
            - generic [aria-hidden]: 
            - text: Send
        - paragraph [ref=e73]: Enter to send · Shift+Enter for a new line
```

# Test source

```ts
  16  | test('register with confirmation enters the workspace', async ({ page }) => {
  17  |   await page.goto('/register')
  18  |   await page.getByLabel('Username').fill('testuser')
  19  |   await page.getByLabel('Email').fill('test@example.com')
  20  |   await page.getByRole('textbox', { name: 'Password', exact: true }).fill('password123')
  21  |   await page.getByRole('textbox', { name: 'Confirm Password' }).fill('password123')
  22  |   await page.getByRole('button', { name: 'Create Account' }).click()
  23  | 
  24  |   await page.waitForURL('http://127.0.0.1:4173/')
  25  |   await page.getByRole('button', { name: /Deploy pipeline/ }).waitFor()
  26  |   expect(await page.getByText('testuser').count()).toBeGreaterThan(0)
  27  | })
  28  | 
  29  | test('registration with mismatched confirmation stays on the form', async ({
  30  |   page,
  31  | }) => {
  32  |   await page.goto('/register')
  33  |   await page.getByLabel('Username').fill('testuser')
  34  |   await page.getByLabel('Email').fill('test@example.com')
  35  |   await page.getByRole('textbox', { name: 'Password', exact: true }).fill('password123')
  36  |   await page.getByRole('textbox', { name: 'Confirm Password' }).fill('different-pass')
  37  |   await page.getByRole('button', { name: 'Create Account' }).click()
  38  | 
  39  |   await expect(page).toHaveURL(/\/register$/)
  40  |   await expect(page.getByRole('alert')).toContainText('Passwords do not match')
  41  | })
  42  | 
  43  | test('unsuccessful login stays on the form with an actionable alert', async ({
  44  |   page,
  45  | }) => {
  46  |   await clearApiRoutes(page)
  47  |   await mockApi(page, { loginStatus: 401 })
  48  |   await page.goto('/login')
  49  |   await page.getByLabel('Username').fill('testuser')
  50  |   await page.getByLabel('Password').fill('wrong-password')
  51  |   await page.getByRole('button', { name: 'Sign In' }).click()
  52  | 
  53  |   await expect(page).toHaveURL(/\/login$/)
  54  |   const alert = page.getByRole('alert')
  55  |   await expect(alert).toBeVisible()
  56  |   await expect(alert).toContainText('Incorrect username or password')
  57  | })
  58  | 
  59  | test('valid login enters the workspace and persists the token', async ({
  60  |   page,
  61  | }) => {
  62  |   await page.goto('/login')
  63  |   await page.getByLabel('Username').fill('testuser')
  64  |   await page.getByLabel('Password').fill('password123')
  65  |   await page.getByRole('button', { name: 'Sign In' }).click()
  66  | 
  67  |   await page.waitForURL('http://127.0.0.1:4173/')
  68  |   expect(await page.evaluate(() => localStorage.getItem('access_token'))).toBe(
  69  |     E2E_TOKEN,
  70  |   )
  71  |   await page.getByRole('button', { name: /Deploy pipeline/ }).waitFor()
  72  | })
  73  | 
  74  | test('loading guard disables the submit button and blocks double submit', async ({
  75  |   page,
  76  | }) => {
  77  |   await clearApiRoutes(page)
  78  |   await mockApi(page, { loginDelay: 600 })
  79  | 
  80  |   let loginRequests = 0
  81  |   const handler = (req: import('@playwright/test').Request) => {
  82  |     if (req.url().includes('/api/auth/login')) loginRequests += 1
  83  |   }
  84  |   page.on('request', handler)
  85  | 
  86  |   await page.goto('/login')
  87  |   await page.getByLabel('Username').fill('testuser')
  88  |   await page.getByLabel('Password').fill('password123')
  89  | 
  90  |   // Submit the form by pressing Enter in the password field (triggers native
  91  |   // form submit → Vue's @submit handler → handleLogin).
  92  |   await page.getByLabel('Password').press('Enter')
  93  | 
  94  |   await page.waitForURL('http://127.0.0.1:4173/')
  95  | 
  96  |   // The form should only have submitted once — the loading guard blocks
  97  |   // double-submit while the request is in flight.
  98  |   expect(loginRequests).toBe(1)
  99  | })
  100 | 
  101 | test('logout leaves the workspace, clears the token, and lands on login', async ({
  102 |   page,
  103 | }) => {
  104 |   await setAuthToken(page)
  105 |   await page.goto('/')
  106 |   await page.getByRole('button', { name: /Deploy pipeline/ }).waitFor()
  107 | 
  108 |   // Expand the sidebar if collapsed (user menu is only visible in expanded mode)
  109 |   const expandBtn = page.getByRole('button', { name: 'Expand sidebar' })
  110 |   if (await expandBtn.isVisible()) {
  111 |     await expandBtn.click()
  112 |   }
  113 | 
  114 |   // Open the user menu by clicking the user button in the sidebar footer
  115 |   const userBtn = page.getByRole('button', { name: 'testuser' })
> 116 |   await userBtn.click()
      |                 ^ Error: locator.click: Test timeout of 60000ms exceeded.
  117 | 
  118 |   // Wait for the Sign Out button to appear in the dropdown
  119 |   const signOutBtn = page.getByRole('button', { name: 'Sign Out' })
  120 |   await expect(signOutBtn).toBeVisible()
  121 |   await signOutBtn.click()
  122 |   await expect(page).toHaveURL(/\/login$/)
  123 |   expect(await page.evaluate(() => localStorage.getItem('access_token'))).toBeNull()
  124 | })
  125 | 
  126 | test('create a conversation, select another, send with Enter, and render the mocked response', async ({
  127 |   page,
  128 | }) => {
  129 |   await setAuthToken(page)
  130 |   await page.goto('/')
  131 |   await page.getByRole('button', { name: /Deploy pipeline/ }).waitFor()
  132 | 
  133 |   // Create a conversation through the sidebar button (immediate creation).
  134 |   await page.getByRole('button', { name: 'New Conversation', exact: true }).click()
  135 |   // The sidebar creates the conversation immediately with the title "New Conversation".
  136 |   // Use aria-label to distinguish the conversation entry from the create button.
  137 |   const newRow = page.getByRole('button', { name: /Open conversation New/ })
  138 |   await expect(newRow).toBeVisible()
  139 |   await expect(newRow).toHaveAttribute('data-active', 'true')
  140 | 
  141 |   // Select an existing conversation; its history renders in the stream.
  142 |   await page.getByRole('button', { name: /Deploy pipeline/ }).click()
  143 |   await page.getByText('Staging deployed successfully.').waitFor()
  144 | 
  145 |   // Send with Enter; the fulfilled SSE fixture renders the response.
  146 |   const composer = page.getByLabel('Message')
  147 |   await composer.fill('Hello agent')
  148 |   await composer.press('Enter')
  149 |   await page.getByText('Hello world').waitFor()
  150 | 
  151 |   // Wait for the status to return to "Idle" (proves the SSE cycle completed
  152 |   // and isStreaming was reset).  The composer text clears on send so checking
  153 |   // the button label "Send" is more reliable than enabled state.
  154 |   await expect(page.getByText('Idle')).toBeVisible()
  155 | 
  156 |   expect(await composer.inputValue()).toBe('')
  157 |   await expect(page.getByRole('button', { name: 'Send' })).toBeVisible()
  158 | })
  159 | 
  160 | test('composer is disabled while streaming and re-enabled after completion', async ({
  161 |   page,
  162 | }) => {
  163 |   await clearApiRoutes(page)
  164 |   await mockApi(page, { chatDelay: 800 })
  165 |   await setAuthToken(page)
  166 |   await page.goto('/')
  167 |   await page.getByRole('button', { name: /Deploy pipeline/ }).click()
  168 |   await page.getByText('Staging deployed successfully.').waitFor()
  169 | 
  170 |   const composer = page.getByLabel('Message')
  171 |   await composer.fill('Stream check')
  172 |   await composer.press('Enter')
  173 | 
  174 |   await expect(page.getByRole('button', { name: /Streaming/ })).toBeDisabled()
  175 |   // "Responding" text only appears in the header (the stream's status says "Agent").
  176 |   await expect(page.getByText('Responding')).toBeVisible()
  177 |   await page.getByText('Hello world').waitFor()
  178 | 
  179 |   // Wait for the status badge to return to "Idle" (proves the SSE cycle completed).
  180 |   await expect(page.getByText('Idle')).toBeVisible()
  181 | 
  182 |   // Button label returns to "Send" after completion (content cleared so enabled
  183 |   // check is unreliable; label is a better signal).
  184 |   await expect(page.getByRole('button', { name: 'Send' })).toBeVisible()
  185 | })
  186 | 
  187 | test('Shift+Enter inserts a newline without sending', async ({ page }) => {
  188 |   let chatRequests = 0
  189 |   await page.route('**/api/chat/*', async (route) => {
  190 |     chatRequests += 1
  191 |     await route.continue()
  192 |   })
  193 |   await setAuthToken(page)
  194 |   await page.goto('/')
  195 |   await page.locator('[data-testid="stream"]').waitFor()
  196 | 
  197 |   const composer = page.getByLabel('Message')
  198 |   await composer.pressSequentially('line1', { delay: 10 })
  199 |   await composer.press('Shift+Enter')
  200 |   await composer.pressSequentially('line2', { delay: 10 })
  201 | 
  202 |   expect(await composer.inputValue()).toBe('line1\nline2')
  203 |   expect(chatRequests).toBe(0)
  204 | })
  205 | 
  206 | test('mocked chat failure shows an actionable alert with Dismiss', async ({
  207 |   page,
  208 | }) => {
  209 |   await clearApiRoutes(page)
  210 |   await mockApi(page, { chatStatus: 500 })
  211 |   await setAuthToken(page)
  212 |   await page.goto('/')
  213 |   await page.getByRole('button', { name: /Deploy pipeline/ }).waitFor()
  214 | 
  215 |   const composer = page.getByLabel('Message')
  216 |   await composer.fill('Trigger failure')
```