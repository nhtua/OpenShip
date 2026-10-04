# Phase 1 UI Repair Implementation Plan
**Type:** plan
**Summary:** Repair the Tailwind v4 styling pipeline, adopt the shadcn-vue-admin shell with PrimeIcons, and restore authenticated-chat behavior.
**Date:** 2026-10-03
**Status:** Proposed; investigation complete, implementation not started.

> **For agentic workers:** Use the `executing-plans` skill to implement this plan task-by-task after the user authorizes implementation. Use `test-driven-development` for application changes and `verification-before-completion` for acceptance. Steps use checkboxes. Do not start implementation merely because this plan exists.

**Goal:** Deliver a modern, consistently styled, accessible Phase 1 admin/chat UI inspired by `Whbbit1999/shadcn-vue-admin`, using PrimeIcons and preserving registration, login, conversation history, and streaming chat.

**Architecture:** First repair the existing Tailwind v4/Vite pipeline and establish semantic light/dark tokens. Add actual local shadcn-vue primitives, then compose a reusable authenticated shell around the existing Pinia stores and router. Refactor presentation incrementally instead of replacing working application logic with mockup code.

**Tech Stack:** Existing Vue 3.5, TypeScript, Vite 6, Tailwind CSS 4, Pinia 2, Vue Router 4, Reka UI, shadcn-vue, PrimeIcons, Vitest/Vue Test Utils; add Playwright for real-browser CSS/layout checks.

**Spec:** [`DESIGN.md`](../../../DESIGN.md), especially sections 2, 3A–C, and 7; [`2026-10-03-phase1-authenticated-chat-design.md`](../specs/2026-10-03-phase1-authenticated-chat-design.md), especially Frontend Design, Authentication Design, and Testing Strategy. This repair implements the Phase 1 subset, not every future feature in `DESIGN.md`.

## Global Constraints

- Work in the existing `feature/phase1-authenticated-chat` worktree. Preserve unrelated changes. Do not reset, rebase, or replace the branch as part of this repair.
- The current request authorizes review and editing this plan only. No application changes, dependency installation, commits, pushes, or PRs during planning. Future execution needs explicit authorization; do not include automatic commit steps.
- `DESIGN.md`: **“Strictly Preserve Base UI Tokens”** — use `--background`, `--foreground`, `--card`, `--border`, and `--accent` through semantic utilities, not hard-coded component colors.
- `DESIGN.md`: **“Non-Blocking UI”** — streaming must remain progressive; loading/error states must remain visible and interactive where safe.
- `DESIGN.md`: **“Follow Vue 3 Best Practices”** — use `<script setup lang="ts">` with typed props/events.
- Keep Tailwind **v4** and the existing `@tailwindcss/vite` integration. Do not add a parallel v3/PostCSS pipeline or a v3 config to patch missing spacing.
- Keep the current Vue Router 4/Pinia 2/backend contracts. The current upstream template uses newer major versions; do not wholesale upgrade OpenShip to copy it.
- The user's explicit **PrimeIcons** preference overrides the Lucide choice in `DESIGN.md`. PrimeIcons is a CSS/font icon pack; it does not require PrimeVue or `@primeuix/themes`.
- Dark mode is the default; provide working light mode through the same semantic tokens. Never use a global `button { ... }` or repeated inline styles to conceal a broken utility pipeline.
- No fake workspace options, invented session IDs, always-running status, dead clickable navigation, or enabled buttons with empty handlers.
- No new backend endpoints, tool execution, workflow builder, artifact inspector/editor, diagram sync, attachments, or reasoning/approval cards in this repair. Preserve extension points through layout boundaries rather than placeholder product UI.
- Preserve auth validation and store success/failure handling. Do not change the auth store to accommodate accidental positional arguments in the views.
- Use test-only users/tokens in fixtures; do not contact a real model provider during UI tests or put credentials in screenshots.

## Review Focus

1. A successful Vite build can still emit incomplete CSS; assert generated rules and real computed padding, dimensions, typography, and token colors (Tasks 1 and 7).
2. Authentication failures are returned as `{ success: false, error }`, not thrown; stay on the form and show an actionable error, including slow/double submissions (Task 3).
3. A narrow viewport, many conversations, or an extremely long message/title must not hide navigation, push the composer off-screen, or create page-level horizontal overflow (Tasks 4–7).
4. Streaming changes the last message's content without changing array length; scrolling must follow new chunks only when the reader is near the bottom, without duplicate assistant placeholders (Task 6).
5. Keyboard/IME users and light-mode users need usable controls: connected labels, visible focus, Enter/Shift+Enter/composition behavior, drawer focus return, and readable colors/icons (Tasks 2–4, 6–7).

---

## 1. Review Findings and Evidence

### Scope of review

- Reviewed `git diff HEAD~3` in the requested worktree, whose HEAD was `3d0f239`.
- The three commits are `d60ac72` (mockup-style UI), `4e95be4` (CSS/icon imports), and `3d0f239` (layout/styling refactor), compared with `ed471a3`.
- The diff changes 14 files, including this plan, `components.json`, dependencies, global CSS, auth views, chat views/components, and `App.vue`.
- The worktree was clean before review. No application source was changed during the investigation.

### P0: Tailwind v3 directives in a Tailwind v4 application — confirmed root cause

`apps/web/package.json` installs Tailwind v4; the lockfile resolves `tailwindcss` and `@tailwindcss/vite` to **4.3.3**. `apps/web/vite.config.js:4–8` already registers the correct Vite plugin. However, `apps/web/src/assets/index.css:1–3` uses:

```css
@tailwind base;
@tailwind components;
@tailwind utilities;
```

Tailwind v4 requires `@import "tailwindcss";`, which includes its default theme, Preflight, and utilities. The old entry point produces a partially styled application: literal-color utilities can compile, but theme-dependent spacing, typography, sizing, radii, and animations are missing.

**Reproduction performed:**

```bash
# From apps/web; builds outside the source tree, without replacing dist/.
pnpm exec vite build --outDir /tmp/opencode/phase1-ui-review-build
```

The build **passes**, but its CSS lacks `--spacing`, the theme/base layers, `.p-4`, `.px-4`, `.gap-3`, `.w-64`, `.text-sm`, and `.rounded-lg`. It contains literal utilities such as `.bg-\[\#0d1117\]`. This explains colors with missing padding, menu spacing, dimensions, and button treatment.

**Single-variable diagnostic:** A programmatic Vite build used a review-only in-memory transform to replace just those three directives with `@import "tailwindcss";`, with `build.write = false`. Without changing any source, the resulting CSS contains `--spacing: .25rem`, `.p-4`, `.px-4`, and `.gap-3`. This confirms the root cause rather than assuming it from the screenshots/symptoms.

### P0/P1: Semantic token integration is missing

- CSS declares `--background`, `--primary`, etc., but has no Tailwind v4 `@theme inline` mapping to `--color-background`, `--color-primary`, and the other semantic colors.
- Even the corrected-import diagnostic still lacks `.bg-primary`; repairing the import alone is not the complete design-system repair.
- Several HSL values are incorrectly annotated as specific hex colors. For example, `222 47% 11%` is not exactly `#0d1117`. Use complete, exact color values rather than copying those inaccurate comments.
- There is no light palette, class-based dark variant, or theme control. Every component repeats literal hex colors, contrary to `DESIGN.md` section 7.
- `--sidebar-background` is the old naming convention; the v4 sidebar components use `--sidebar` via `bg-sidebar`. Align token names with the chosen component generation.

### P1: Installing shadcn packages did not install styled components or the admin shell

- `components.json` is generator configuration; it is not a CSS theme or a component implementation.
- There is no `src/components/ui/` or `src/lib/utils.ts` in the reviewed source, and none of the views uses shadcn primitives.
- Reka UI is a headless accessibility/behavior foundation; installing it does not style existing native HTML buttons.
- `ChatView.vue` contains an imitation sidebar in one large view instead of the template's provider/sidebar/inset/header composition.
- Missing `min-w-0`, `min-h-0`, appropriate shrink rules, and responsive navigation will remain layout problems after CSS compilation is fixed.

### P1: The visual rewrite broke auth behavior

- `LoginView.vue` calls `auth.login(username, password)`, but `stores/auth.ts:19` expects one `LoginRequest` object.
- `RegisterView.vue` calls `auth.register(username, email, password)`, but `stores/auth.ts:40` expects one `RegisterRequest` object.
- The store catches API errors and returns a success result; the new views assume rejection and redirect after any resolved result.
- Labels lost `for`/`id`, inputs lost `name`/`required`, loading disabling was removed, and registration lost password confirmation and validation.
- The `build` script runs Vite only. It transpiles TypeScript but does not typecheck Vue SFCs, so these errors are not a build gate.

### P1: Conversation controls and actionable errors disappeared

- `ChatView.vue:10–36` still defines creation/selection state and handlers, but its template no longer renders a conversation list or New Conversation form.
- It fetches conversations that the user cannot access through the UI.
- The previous `chat.error` banner was removed.
- Home and Agent Workspace are both permanently active; navigation items are non-keyboard-operable `div`s with no route or action.
- Workspace options, `Provision & Build`, and fallback session `1042` are mock data. Export/Stop handlers are empty, and Running is always shown.
- Sign Out clears auth state but does not navigate away from the already-mounted protected workspace. The router guard only checks when navigation occurs. This navigation gap predates the three reviewed commits; address it while integrating the new shell, rather than attributing it to the styling rewrite.

### P2: Chat usability and accessibility regressed

- `ChatStream.vue` removed the scroll container ref and watchers. Neither new messages nor streaming chunks trigger follow-to-bottom.
- `AgentMessage.vue` accepts `isStreaming` but never uses it. `ChatStream.vue` adds a second assistant/loading row alongside the existing empty assistant placeholder.
- Message wrapping rules were removed; long URLs/unbroken output can overflow.
- The composer now uses a textarea (a good direction), but lost its form/submit semantics and accessible labeling. Keyboard handling does not protect IME composition.
- Required boolean props now produce warnings when omitted; preserve defaults for `disabled` and `isStreaming`.

### P2: Icon and dependency issues

- `primeicons/primeicons.css` is the correct installed import; the old plan's `@primeuix/themes/primeicons/primeicons.css` path is wrong.
- Installed PrimeIcons **8.0.2** defines `pi-send` and `pi-comments` but **does not define `pi-anchor`**. The current logo icon will be blank even after the spacing repair.
- Retain the installed `cn` package: upstream uses `export { cn } from 'cn'`, and its installed version supports Tailwind class merging. It is not a typo for a missing local helper.
- `@radix-ui/themes` is a React package, not the Vue shadcn theme. Do not add its stylesheet as a fix. `motion-v`, auto-animate, and Lucide are not currently used by application source, but dependency removal is not required to repair CSS; audit before any cleanup.

### Test baseline

```bash
pnpm exec vitest run
```

Observed: **36 tests; 14 pass, 22 fail**. `ChatSSE.test.ts` passes both tests; `AuthPages.test.ts` has 10 failures and `ChatComponents.test.ts` has 12. Some failures are stale structural selectors (`input` → `textarea`, missing `.message`, heading changes); others expose real removed behavior (validation, loading, conversation controls). Do not delete tests or weaken behavioral assertions just to obtain green output.

The existing ChatView tests also attempt real requests and log `ECONNREFUSED`; add deterministic API mocks. `vue-tsc` is not installed. No actual-browser screenshot/computed-style review was performed during planning; Task 7 supplies that missing evidence.

### Investigation checklist

- [x] Read recent changes and trace the CSS entry point through Vite to generated CSS.
- [x] Reproduce missing generated utilities with a fresh build.
- [x] Compare against a working CSS entry point in `mockup/src/index.css` and the upstream template.
- [x] Test the import hypothesis using a single-variable in-memory build.
- [x] Trace view/store interfaces and run the existing frontend tests.
- [x] Specify an ordered repair and verification plan without implementing it.

## 2. Reference and Design Decisions

### Reference snapshot

Use upstream commit **`909de27cda50daa3bf97e7279b25433374aeb651`**, reviewed on 2026-10-03, as a reference for these files:

- `src/assets/index.css`: v4 imports, dark variant, semantic theme mapping, base layer.
- `src/layouts/default.vue`: `SidebarProvider` → `AppSidebar` + `SidebarInset` → header/main.
- `src/components/app-sidebar/index.vue`: header/content/footer groups and collapsible navigation.
- `src/components/ui/sidebar/{SidebarProvider.vue,Sidebar.vue,utils.ts}`: desktop/mobile state, widths, drawer behavior.
- `src/components/ui/button/{Button.vue,index.ts}`: actual styled button variants and focus/disabled treatment.
- `src/lib/utils.ts`: class-merging helper export.

Reference URL format: `https://github.com/Whbbit1999/shadcn-vue-admin/blob/909de27cda50daa3bf97e7279b25433374aeb651/<path>`.

Read the relevant reference files completely before adapting them. Preserve MIT attribution if copying upstream code. Do not import its demo users, teams, authentication, routing plugins, charts, localization, cookie integrations, or full dependency tree.

### Selected approach and alternatives

**Selected:** Repair the existing v4 foundation, add a small local shadcn component set, and adapt the admin shell around the existing application. This addresses the root cause without losing working auth/SSE code.

**Not selected:** More literal colors/inline padding in the current templates. This only treats symptoms and duplicates styles.

**Not selected:** Replace `apps/web` with the upstream application. This would unnecessarily replace routing/state/auth contracts and upgrade framework tooling.

### Visual contract

- GitHub-inspired dark surfaces retained from the mockup, but defined centrally as tokens. Match the template's hierarchy and geometry rather than reproducing every demo feature.
- Desktop sidebar: **16rem expanded / 3rem icon-only**. Mobile: accessible drawer below **768px**, maximum **18rem**, capped to `calc(100vw - 2rem)`.
- Header: **56px** minimum height, **16px** horizontal padding on mobile / **24px** on desktop; controls may wrap instead of forcing overflow.
- Body text: **14px / 20px**; message text: **14px / 24px**; page title: **18px / 28px**, semibold; supporting labels: **12px / 16px**.
- Default buttons/inputs: **36px** minimum height; use at least **44px** for touch navigation/composer controls below 768px. Button icon/text gap **8px**.
- Forms: outer gutter **16px**, card max width **28rem**, card padding **24px** on mobile / **32px** on desktop, field gap **16px**, label gap **8px**.
- Chat column: centered, max width **48rem**, stream padding **16px** mobile / **24px** desktop, row gap **24px**; bubbles padded **16px horizontal / 12px vertical**, radius **8px**.
- Sidebar rows: **12px horizontal / 8px vertical** padding, icon gap **12px**, one active route. Conversation titles truncate; full value is available in the accessible name/title.
- Border: **1px** using `border-border`; radius token **0.5rem**. Subtle shadows only for floating/card surfaces; no unnecessary gradients or large decorative effects.
- Primary action blue, secondary/outline neutral, destructive red, success reserved for meaningful success. Idle chat is not Executing/Running.

### Exact token palette

Store **complete CSS colors** (hex below), not bare HSL channels. `@theme inline` maps `--color-<name>: var(--<name>)`; do not wrap these hex variables in `hsl()`.

| Token | Light `:root` | Dark `.dark` |
|---|---|---|
| background | `#ffffff` | `#0d1117` |
| foreground | `#1f2328` | `#e6edf3` |
| card / popover | `#ffffff` | `#161b22` |
| card-foreground / popover-foreground | `#1f2328` | `#e6edf3` |
| primary / ring | `#0969da` | `#58a6ff` |
| primary-foreground | `#ffffff` | `#0d1117` |
| secondary / muted / accent | `#f6f8fa` | `#21262d` |
| secondary-foreground / accent-foreground | `#1f2328` | `#e6edf3` |
| muted-foreground | `#59636e` | `#8b949e` |
| destructive | `#cf222e` | `#f85149` |
| destructive-foreground | `#ffffff` | `#0d1117` |
| border / input | `#d1d9e0` | `#30363d` |
| success | `#1a7f37` | `#3fb950` |
| success-foreground | `#ffffff` | `#0d1117` |
| sidebar | `#f6f8fa` | `#161b22` |
| sidebar-foreground | `#1f2328` | `#e6edf3` |
| sidebar-primary / sidebar-ring | `#0969da` | `#58a6ff` |
| sidebar-primary-foreground | `#ffffff` | `#0d1117` |
| sidebar-accent | `#eaeef2` | `#21262d` |
| sidebar-accent-foreground | `#1f2328` | `#e6edf3` |
| sidebar-border | `#d1d9e0` | `#30363d` |

Map every listed token, including foreground pairs and sidebar tokens. Define `--radius: 0.5rem`, with `--radius-sm: calc(var(--radius) - 4px)`, `--radius-md: calc(var(--radius) - 2px)`, `--radius-lg: var(--radius)`, and `--radius-xl: calc(var(--radius) + 4px)` inside `@theme inline`.

Light/dark contrast must be checked in a browser; token border color alone is not sufficient focus indication. Use a visible ring for focus and combine status color with text.

## 3. File Responsibilities and Execution Order

Full file paths below are relative to the worktree root; shortened `src/` and tooling paths in the responsibility table are relative to `apps/web`. Commands within tasks run from `apps/web` unless stated otherwise.

| Files | Responsibility |
|---|---|
| `src/assets/index.css`, `src/main.ts`, `index.html`, `src/App.vue` | One v4 CSS entry point, shared tokens, root theme/height setup |
| `src/lib/utils.ts`, `src/components/ui/*`, `components.json` | Local shadcn-vue primitives and class merging |
| `src/components/common/Icon.vue`, `src/components/common/icons.ts` | Typed PrimeIcons names, consistent size, decorative semantics |
| `src/views/LoginView.vue`, `src/views/RegisterView.vue`, `src/layouts/AuthLayout.vue` | Accessible auth forms using existing store contracts |
| `src/layouts/AppLayout.vue`, `src/components/layout/AppHeader.vue`, `src/components/layout/AppSidebar.vue` | Authenticated responsive shell; no API requests in presentational layout |
| `src/stores/theme.ts` | Shared persisted light/dark preference |
| `src/views/ChatView.vue`, targeted `src/stores/chat.ts` changes | Orchestrate actual conversations/errors/status; preserve SSE transport |
| `src/components/chat/{ChatStream,ChatInput,UserMessage,AgentMessage}.vue` | Scroll, compose, messages, streaming presentation |
| `tests/*.test.ts`, `tests/helpers/*` | Behavioral and generated-CSS regression tests |
| `playwright.config.ts`, `tests/e2e/*` | Browser-computed styles, responsive layout, icons, screenshots |
| `package.json`, `pnpm-lock.yaml`, `tsconfig.json`, `src/env.d.ts`, `README.md` | Minimal test/tooling setup and documented verification |

Dependency order: **1 → 2 → 3 → 4 → 5 → 6 → 7**. Tasks 3 and 4 both consume the primitives from Task 2. Do not polish individual templates before Task 1's CSS assertions pass.

## 4. Step-by-Step Implementation Tasks

### Task 1: Repair and regression-test the Tailwind v4 foundation

**Files:** Modify `apps/web/src/assets/index.css`, `apps/web/src/main.ts`, `apps/web/src/App.vue`, `apps/web/index.html`; create `apps/web/tests/StylingPipeline.test.ts`.

**Interfaces:** Produces semantic color/radius utilities and a single CSS entry point consumed by every later component. Does not change any store, API, or route.

- [ ] **1.1 Record the baseline.** Run `git status --short`, `pnpm exec vitest run`, and `pnpm build`; save the known failures in execution notes. Confirm `vite.config.js` still has `plugins: [vue(), tailwindcss()]`. Do not interpret a successful build as correct styling.
- [ ] **1.2 Write `StylingPipeline.test.ts`.** Use `// @vitest-environment node`, import Vite's `build`, and build the real application with `configFile` pointing at `vite.config.js`, `build.write: false`, `build.minify: false`, and `build.cssMinify: false`. Inspect returned CSS assets, not the existing `dist/`. Assert the CSS contains `--spacing`, rules for `.p-4`, `.px-4`, `.gap-3`, `.text-sm`, `.rounded-lg`, and a Preflight/base layer. Add named tests `generatesDefaultThemeUtilities` and `generatesSemanticRootColors`; the latter asserts `.bg-background` / `.text-foreground` resolve to the semantic variables once the root wrapper is added. Run only this file; expect missing-utility failures before the repair.

  Core assertions on the joined, unminified CSS assets:

  ```ts
  expect(css).toContain('--spacing:');
  for (const selector of ['p-4', 'px-4', 'gap-3', 'text-sm', 'rounded-lg']) {
    expect(css).toMatch(new RegExp(`\\.${selector}\\s*\\{`));
  }
  expect(css).toMatch(/@layer base\s*\{/);
  expect(css).toMatch(/\.bg-background\s*\{[^}]*var\(--background\)/);
  expect(css).toMatch(/\.text-foreground\s*\{[^}]*var\(--foreground\)/);
  ```

- [ ] **1.3 Replace the v3 directives with `@import "tailwindcss";` at the beginning of `index.css`.** Keep the existing Vite plugin; do not add another CSS processor. Re-run `generatesDefaultThemeUtilities` and confirm spacing/radius/type rules now exist.
- [ ] **1.4 Add the exact palette above with `:root`, `.dark`, `@custom-variant dark (&:is(.dark *));`, and `@theme inline`.** Map every semantic color and radius. Put global border/outline defaults and `body` background, foreground, system sans font, `text-sm leading-5`, and antialiasing in `@layer base`. Set `color-scheme: light` in `:root` and `color-scheme: dark` in `.dark` so native controls match. Remove the old bare-HSL tokens and unlayered hard-coded body colors.
- [ ] **1.5 Set up the root and icons.** Keep `App.vue` as a thin `RouterView` wrapper with `min-h-dvh bg-background text-foreground`; no duplicate app header. Set the initial `<html>` class to `dark`. Import `primeicons/primeicons.css` exactly once in `main.ts` alongside `./assets/index.css`; remove the CSS-level icon import to avoid ordering ambiguity.
- [ ] **1.6 Run `pnpm exec vitest run tests/StylingPipeline.test.ts` and `pnpm build`.** Both should pass. Check that the output references a PrimeIcons font asset. Existing auth/chat test failures remain explicitly tracked for Tasks 3–6.

### Task 2: Add real shadcn primitives, PrimeIcons, and a Vue typecheck gate

**Files:** Modify `apps/web/package.json`, `apps/web/pnpm-lock.yaml`, `apps/web/components.json`, `apps/web/tsconfig.json`; create `apps/web/src/env.d.ts`, `apps/web/src/lib/utils.ts`, `apps/web/src/components/common/{Icon.vue,icons.ts}`, local `apps/web/src/components/ui/*`, and `apps/web/tests/UIPrimitives.test.ts`.

**Interfaces:** `@/lib/utils` exports `cn` from the installed `cn` package. UI barrels export `Button`, `Input`, `Textarea`, `Label`, `Card` family, `Badge`, `Alert` family, `Separator`, `Tooltip` family, `Sheet` family, and `Sidebar` family. `Icon.vue` accepts `{ name: PrimeIconName; class?: HTMLAttributes['class'] }` and renders an `aria-hidden="true"` icon; its surrounding control owns the accessible name.

- [ ] **2.1 Write failing primitive tests.** `buttonForwardsNativeAttributes` asserts `type="submit"`, `disabled`, and `aria-label` reach the real button and no click action executes when disabled. `inputAndTextareaSupportVModel` asserts text updates emit `update:modelValue` and `id`/`name` reach native fields. `cnMergesConflictingUtilities` checks `cn('p-2', 'p-4') === 'p-4'`. `iconsUseOnlyInstalledGlyphs` reads the installed pack CSS and validates every allowed name has a glyph selector. Run `pnpm exec vitest run tests/UIPrimitives.test.ts`; expect missing imports/components before implementation.
- [ ] **2.2 Install only the required additional tooling after implementation authorization.** Add `vue-tsc` and explicit `@types/node` as dev dependencies, using versions compatible with the current TypeScript/Vite versions. Add `tw-animate-css` for the v4 component animation utilities. Do not upgrade Vue/Pinia/router/Vite majors. Commit nothing; update the lockfile through pnpm, not hand editing.
- [ ] **2.3 Create `src/lib/utils.ts` with `export { cn } from 'cn'`.** Retain `cn`, `class-variance-authority`, and `reka-ui`. Do not install a second class-merging solution without an identified compatibility problem.
- [ ] **2.4 Generate the minimal component set.** Run `pnpm exec shadcn-vue add button input textarea label card badge alert separator tooltip sheet sidebar` using the existing local CLI. Do not run `init`, accept a global template overwrite, or overwrite the repaired CSS. Inspect the generated dependency closure and `components.json`; aliases must point at real directories and the CSS path must remain `src/assets/index.css`. An empty Tailwind config path is correct for v4. Treat generator `iconLibrary` metadata as scaffolding metadata, not PrimeIcons runtime configuration; do not invent an unsupported `primeicons` generator value.
- [ ] **2.5 Normalize generated files.** Replace generated Lucide icon imports in sheet/sidebar controls with the shared `Icon` component; keep Reka behaviors and attribute forwarding. Import `tw-animate-css` after Tailwind in the global stylesheet and stop relying on the v3 `tailwindcss-animate` plugin. Remove that plugin dependency only after confirming nothing uses it. Adapt any generated class-merging helper to the agreed `cn` export without replacing unrelated generated logic.
- [ ] **2.6 Implement the typed icon adapter.** Export `PRIME_ICON_NAMES` as a readonly tuple containing `comments`, `plus`, `user`, `lock`, `envelope`, `sign-out`, `bars`, `chevron-left`, `chevron-right`, `times`, `sun`, `moon`, `spinner`, `info-circle`, `arrow-down`, and `send`; derive `PrimeIconName` from that tuple. Validate this list against the installed CSS before use. Render `pi pi-${name}` with a default `text-base leading-none shrink-0` class and merged overrides. Replace the nonexistent anchor with a typographic **OS** logo mark; do not choose another unverified glyph.
- [ ] **2.7 Define the shared Button geometry.** In the button variant definitions, default height is `h-9`, horizontal padding `px-4`, gap `gap-2`, and semantic variant colors/focus rings. Do not rely on upstream SVG-only selectors to size font icons. Destructive buttons must use `text-destructive-foreground`, not unconditional `text-white`, with the selected dark palette. Preserve `as-child` composition for links and native disabled/type semantics.
- [ ] **2.8 Add `typecheck: "vue-tsc --noEmit -p tsconfig.json"` and `test:run: "vitest run"` scripts.** Add `src/env.d.ts` with the Vite client type reference. Remove the application tsconfig's unnecessary reference to the no-emit `tsconfig.node.json` if it causes an invalid referenced-project check; do not silence checking with `any`, `@ts-ignore`, or by excluding the auth views. Keep tests outside the application's source typecheck.
- [ ] **2.9 Verify the primitives and build.** Run `pnpm exec vitest run tests/UIPrimitives.test.ts tests/StylingPipeline.test.ts` and `pnpm build`. Run `pnpm typecheck` to expose the known auth argument and unused-handler errors; record them and resolve them in the owning tasks. Typecheck must be fully green at the end, not necessarily before those repairs.

### Task 3: Restore correct auth behavior in polished, accessible forms

**Files:** Modify `apps/web/src/views/{LoginView,RegisterView}.vue`, `apps/web/tests/AuthPages.test.ts`; create `apps/web/src/layouts/AuthLayout.vue` and `apps/web/tests/helpers/mountWithRouter.ts`. Preserve `apps/web/src/stores/auth.ts` and `apps/web/src/services/api.ts` contracts.

**Interfaces:** `AuthLayout.vue` consumes `{ title: string; description: string }` plus default/footer slots. `LoginView.handleLogin(): Promise<void>` calls `auth.login({ username, password })`. `RegisterView.validate(): RegisterRequest | null` feeds `auth.register(data)`. Navigate to `{ name: 'chat' }` only after `result.success === true`.

The test helper exports `mountWithRouter(component: Component, initialPath: string): Promise<{ wrapper: VueWrapper; router: Router; pinia: Pinia }>`; use `useAuthStore(pinia)` / `useChatStore(pinia)` to access the same stores as the mounted component.

- [ ] **3.1 Make auth tests deterministic.** The mounting helper creates a fresh Pinia and memory router with named `login`, `register`, and `chat` routes, mounts with both plugins, awaits router readiness, and exposes wrapper/router/stores. Clear localStorage and restore mocks after each test. Mock auth actions/API calls; do not make network requests.
- [ ] **3.2 Add/retain failing behavior tests before changing the views.** `loginPassesRequestObject` expects `{ username: 'testuser', password: 'password123' }`. `failedLoginStaysOnForm` returns `{ success: false, error: 'Invalid credentials' }` and asserts the route is still login and a `role="alert"` contains the error. Test successful navigation separately. Mirror these tests for registration. `pendingAuthDisablesDuplicateSubmit` asserts a second submit does not invoke the action again while pending and the submit button is disabled. Run `pnpm exec vitest run tests/AuthPages.test.ts -t 'LoginView|RegisterView'`; expect failures on current code.

  Failure-path test contract after filling the named fields, submitting, and awaiting `flushPromises()`:

  ```ts
  expect(loginSpy).toHaveBeenCalledWith({
    username: 'testuser', password: 'password123',
  });
  expect(router.currentRoute.value.name).toBe('login');
  expect(wrapper.get('[role="alert"]').text()).toContain('Invalid credentials');
  ```

- [ ] **3.3 Restore validation and field semantics.** Login has required username/password. Registration has trimmed username/email, valid email, password at least **8** characters, and a matching confirmPassword. Restore the messages `Username is required`, `A valid email is required`, `Password must be at least 8 characters`, and `Passwords do not match`. Invalid registration must not call the store. Keep `id`, `name`, `for`, `required`, and appropriate autocomplete values: username, email, current-password/new-password. Use `aria-invalid` and `aria-describedby` for invalid fields. Preserve the existing validation tests and add blank-username/invalid-email/no-call assertions.
- [ ] **3.4 Repair the async handlers.** Read `{ success, error }` returned by the existing store; display `localError ?? auth.error` in a styled alert and navigate only on success. Use `auth.isLoading` plus a local submission guard for direct repeated submit events. Do not catch Axios errors in the views as the primary path because the store already handles them.
- [ ] **3.5 Build `AuthLayout` and restyle the forms with shared primitives.** Use semantic backgrounds, Card, Label, Input, and Button; apply the exact form spacing contract. Keep **OpenShip** branding separate from a real **Sign In** / **Create Account** heading (retain `h2` selectors used by existing tests). Render decorative icons beside fields without overlapping text; icon-offset inputs need `pl-10`. Restore disabled/loading labels **Signing in...** and **Creating account...**, and visible registration/login links with keyboard focus styling.
- [ ] **3.6 Verify auth tests and affected types.** Run the filtered auth suite, then `pnpm typecheck`. Auth argument errors must be gone. Any remaining ChatView unused handlers belong to Task 5; do not exclude files to conceal them.

### Task 4: Compose the responsive admin shell and working themes

**Files:** Create `apps/web/src/layouts/AppLayout.vue`, `apps/web/src/components/layout/{AppHeader,AppSidebar}.vue`, `apps/web/src/stores/theme.ts`, `apps/web/tests/{AppLayout,Theme}.test.ts`; modify `apps/web/src/main.ts`, and generated sidebar breakpoint/width files as needed.

**Interfaces:**

- `AppLayout` owns `SidebarProvider` and supplies named `sidebar`/`header` slots and a default content slot; it does not fetch data.
- `AppHeader` props: `{ title: string; isStreaming: boolean; username: string | null }`; emits `signOut: []`. It contains SidebarTrigger and theme control, not empty Export/Stop actions.
- `AppSidebar` props: `{ conversations: Conversation[]; currentConversationId: string | null; busy: boolean }`; emits `createConversation: [title: string]` and `selectConversation: [id: string]`.
- `useThemeStore` exposes `mode: 'light' | 'dark'`, `initializeTheme(): void`, and `toggleTheme(): void`. Storage key: `openship-theme`; missing/invalid value defaults to dark.

- [ ] **4.1 Write shell/theme tests first.** Test named navigation/header/main landmarks, exactly one `aria-current="page"` route, sidebar create/select emissions, no fake workspace/session text or enabled no-op controls, and disabled conversation mutations while busy. Theme tests assert initial dark class, persisted light initialization, invalid storage fallback, and toggling the root class plus storage. Run both files and record expected missing-component failures.
- [ ] **4.2 Implement `useThemeStore` and initialize before mounting.** Toggle `document.documentElement.classList` and persist the explicit preference; catch storage access errors so theme selection cannot crash the app. Test a throwing storage mock. Use the shared state in the header toggle with an accessible action label (**Switch to light mode** / **Switch to dark mode**). No system-theme picker is required for this repair.
- [ ] **4.3 Adapt the template's provider/sidebar/inset composition.** Root authenticated layout is `h-dvh min-h-0 w-full overflow-hidden`; inset/content ancestors are `min-h-0 min-w-0 flex-1 flex flex-col`. Header/footer do not shrink; the stream is the scrolling region. Override the provider's default `min-h-svh` when necessary so the child flex chain can shrink. Do not stack the old root header and the new shell header.
- [ ] **4.4 Implement responsive sidebar behavior.** Use the shadcn sidebar's desktop collapse state and Sheet-backed mobile drawer, not a permanently visible 256px aside. Align its JS media query to **max-width: 767px** and CSS `md` breakpoint at **768px**; verify the boundary rather than retaining upstream's overlapping 768px checks. Apply 16rem/3rem desktop widths and a mobile width cap. Drawer needs a title/description, focus trapping, Escape close, and focus return to the trigger.
- [ ] **4.5 Populate only real Phase 1 navigation.** Agent Workspace uses `RouterLink` to the named chat route with actual active state. Put **New Conversation** and the scrollable conversation list in the sidebar. Use native buttons inside sidebar primitives, not clickable divs. Local create form defaults a blank title to **New Conversation**, provides Create/Cancel, and emits once. In icon-only mode, use a labeled **Expand sidebar** control to reopen the full conversation list; do not make history inaccessible or add a second custom navigation system. Hide unimplemented workflows/tools/logs/settings links rather than creating routes for them.
- [ ] **4.6 Implement the header and footer hierarchy.** Header uses the current title or **New Conversation**, status **Responding** only when streaming and **Idle** otherwise, the theme toggle, and authenticated user information when available. Sign Out emits to the view. Use text plus badge color; reduced-motion users must not depend on pulsing dots. Do not add fabricated workspace selectors or session labels.
- [ ] **4.7 Run `pnpm exec vitest run tests/AppLayout.test.ts tests/Theme.test.ts tests/UIPrimitives.test.ts`.** Verify semantic/class behavior in unit tests; reserve dimensions, breakpoint rendering, focus trapping, and overflow for Task 7's real browser tests.

### Task 5: Reconnect the shell to actual conversations, errors, and logout

**Files:** Modify `apps/web/src/views/ChatView.vue`, `apps/web/src/stores/chat.ts`, ChatView tests in `apps/web/tests/AuthPages.test.ts`; create `apps/web/tests/ChatView.test.ts` and `apps/web/tests/helpers/chatFixtures.ts` as needed.

**Interfaces:** Preserve `getConversations(): Promise<void>`, `createConversation(title: string): Promise<Conversation | null>`, `loadConversation(id: string): Promise<void>`, and `sendMessage(content: string, conversationId?: string | null)`. `ChatView` handles shell events and passes store state to chat components. Loading metadata changes do not replace the SSE protocol.

- [ ] **5.1 Write integration-level view tests before refactoring.** Mock `api.get`/`api.post` or Pinia actions. `rendersConversationHistory` asserts fixture titles and active selection. `createsAndSelectsConversation` verifies one create call with a trimmed/default title, then a load call with the returned ID. `showsActionableChatError` asserts a named alert with the store error, Dismiss, and a conversation-list Retry when history loading failed. `logoutLeavesWorkspace` asserts token removal, cleared chat state, and navigation to login. Test disabled selection/creation during streaming. Run `pnpm exec vitest run tests/ChatView.test.ts`; expect behavior failures on current code.
- [ ] **5.2 Replace only the ChatView presentation/orchestration needed for the shell.** Wire AppSidebar events to existing store methods, AppHeader's signOut to logout, and ChatInput's send to `sendMessage(content, currentConversation?.id ?? null)`. Keep mounted history loading. Remove the now-obsolete static nav array, mock workspace controls, `searchQuery`, `showNotifications`, and empty export/stop handlers only as their presentation is replaced. Do not delete unrelated code or store capabilities.
- [ ] **5.3 Make async states explicit.** View-local refs `isLoadingConversations`, `isCreatingConversation`, and `isLoadingConversation` drive loading indicators and guards; use `finally` to release them. A `historyLoadFailed` ref controls history Retry: clear `chat.error` before the guarded history request, then set this ref from the resulting error because `getConversations` catches failures internally. Do not infer failure from an empty conversation list. Sidebar `busy` is streaming/creating/loading-selection. Show **Loading conversations...**, **No conversations yet**, or an actionable error as appropriate. Keep error rendering outside the stream's scrolling content so failures do not disappear below history.
- [ ] **5.4 Preserve actual conversation metadata on selection.** In `loadConversation`, resolve the chosen Conversation from the loaded list instead of replacing it with an empty title. Fetch messages before replacing the displayed conversation/messages; on failure retain the previous selection and expose the error. Use a fallback title **Conversation** only if metadata is unavailable, never mock session copy. Add store tests `loadConversationKeepsKnownTitle` and `failedConversationLoadKeepsPreviousSelection` using mocked API responses. Streaming blocks selection, so do not introduce cancellation/race machinery unrelated to this repair.
- [ ] **5.5 Implement logout and errors.** On logout, call `auth.logout()`, `chat.clearMessages()`, clear `chat.conversations` and `chat.error`, and call `router.replace({ name: 'login' })` so previous-user history cannot remain visible after the next login. Keep a semantic error Alert with Dismiss; history Retry calls `getConversations` without resending a message. Do not blindly retry POST/send on every error. Stop/export remain absent until there is an independently specified behavior and transport implementation.
- [ ] **5.6 Reconcile the old ChatView tests with the real shell.** Preserve New Conversation, username visibility, and empty-chat expectations. Mount with a router and mock APIs so no test contacts localhost. Run `pnpm exec vitest run tests/AuthPages.test.ts tests/ChatView.test.ts tests/ChatSSE.test.ts` and `pnpm typecheck`. Auth/view checks should be green; remaining old composer/message tests are resolved next.

### Task 6: Polish chat messages/composer and restore stream usability

**Files:** Modify `apps/web/src/components/chat/{ChatStream,ChatInput,UserMessage,AgentMessage}.vue`, `apps/web/tests/ChatComponents.test.ts`; extend `apps/web/tests/ChatSSE.test.ts` without deleting its existing tests.

**Interfaces:** `ChatInput` props `{ disabled?: boolean }` default false; emits `send: [content: string]`. `AgentMessage` props `{ message: ChatMessage; isStreaming?: boolean }` default false. `ChatStream` props `{ messages: ChatMessage[]; isStreaming: boolean }`. Retain a `.message` class and add stable `data-testid` values for stream/composer/bubbles; these are test hooks, not the source of styling.

- [ ] **6.1 Reconcile the textarea change without weakening behavioral tests.** Change old composer selectors from `input` to `textarea`, but continue asserting a form and `button[type="submit"]`, trimming, exactly-once submit, disabled behavior, and streaming feedback. Add `ignoresEnterDuringComposition`, `shiftEnterDoesNotSend`, `disabledSubmitDoesNotClearDraft`, and `whitespaceDoesNotSend`. Run the composer subset and confirm current failures first.

  The existing trim/submit test should retain these assertions after switching to the textarea:

  ```ts
  await wrapper.get('textarea[name="message"]').setValue('  hello  ');
  await wrapper.get('form').trigger('submit');
  expect(wrapper.emitted('send')).toEqual([['hello']]);
  expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('');
  ```

- [ ] **6.2 Restore semantic composition.** Render a form with `@submit.prevent`, a labeled Textarea (`id/name="message"`, visible or screen-reader-only **Message** label), and shared Button. Guard `handleSend` for disabled/blank content; clear only after emitting a valid send. Enter without Shift submits once and prevents a newline; Shift+Enter inserts a newline; composition (`event.isComposing`, with keyCode 229 compatibility) never submits. Preserve the draft while disabled. Keep the textarea multiline and grow it to its scrollHeight, capped at **192px** (`max-h-48`), then scroll internally. Add a concise **Enter to send · Shift+Enter for a new line** hint.
- [ ] **6.3 Specify deterministic scrolling tests.** Mock stream `scrollHeight`, `clientHeight`, and `scrollTop`. `followsStreamingContentNearBottom` changes the last assistant content without changing array length and expects a scroll after `nextTick`. `doesNotForceScrollWhenReadingHistory` simulates more than **48px** from the bottom and expects unchanged scrollTop. `jumpToLatestResumesFollowing` clicks a named button and verifies the next chunk follows. Initial loaded history should scroll to bottom; empty state should be centered.
- [ ] **6.4 Implement one stream scroll owner.** Restore a container ref and post-render watchers for message count, last-message content, and streaming state. Track the reader's bottom proximity on scroll using the **48px** threshold; only auto-follow when near the bottom or on initial history load. Render **Jump to latest** when away from the bottom. Keep header/composer pinned via the Task 4 flex chain and the stream's `flex-1 min-h-0 overflow-y-auto`; do not scroll the entire page on every chunk.
- [ ] **6.5 Make assistant streaming presentation single-source.** The existing store already creates an assistant placeholder with `id: ''`. Pass `isStreaming` to that last assistant message; show **Agent** plus a thinking indicator when content is empty, and a subtle cursor/status when chunks exist. Remove the extra loading row in ChatStream. Test exactly one assistant row before/after the first chunk, final text after completion, and no indicator when idle. Add `rendersChunksBeforeCompletion` to `ChatSSE.test.ts`: use a controlled ReadableStream, enqueue `He` and then `llo` in separate reads, assert content `He` then `Hello` while `isStreaming` remains true, then enqueue completion and close; assert the final ID and idle state. No real timers or provider calls are needed. Handle `messages=[]` with `isStreaming=true` with one standalone status, never a second placeholder. Keep an accessible conversation log and do not make the entire chat region `aria-busy` during streaming.
- [ ] **6.6 Apply message geometry and wrapping.** User messages align right with a semantic primary-tinted bubble; assistant messages align left with an **AI** mark and `bg-card border-border`. Use `min-w-0`, `max-w-full`, `whitespace-pre-wrap`, and `overflow-wrap:anywhere` on message text. Retain `.message` hooks and message roles. Add long unbroken-text/newline fixtures and preserve Vue's escaped interpolation; do not use raw `v-html` or add markdown rendering as part of this repair.
- [ ] **6.7 Apply shared composer/message styling and motion safeguards.** Use the visual contract, semantic tokens, PrimeIcons Send/spinner, visible focus, and disabled state. Streaming button text is **Streaming...**, not an idle Send with no feedback. Keep functional status text even when `prefers-reduced-motion` disables animated dots/spinner motion.
- [ ] **6.8 Run the full unit/build/typecheck gates.** Run `pnpm test:run`, `pnpm typecheck`, and `pnpm build`. All tests must pass with no missing-required-prop warnings or real network requests. Both existing SSE tests must still pass. A green class-based/unit suite is necessary but not sufficient for visual acceptance.

### Task 7: Verify real styling, responsive layout, and visual quality in a browser

**Files:** Modify `apps/web/package.json`, `apps/web/pnpm-lock.yaml`, `apps/web/vitest.config.ts`, `apps/web/README.md`; create `apps/web/playwright.config.ts`, `apps/web/tests/e2e/{ui.spec.ts,auth-chat.spec.ts}`, `apps/web/tests/e2e/fixtures/api.ts`, and reviewed screenshot baselines.

**Interfaces:** Browser tests run the real Vite application on a fixed localhost port, with deterministic `/api` route fixtures. They consume the real CSS/font assets and component tree; no Tailwind CDN, synthetic replacement stylesheet, or external model calls.

- [ ] **7.1 Add Playwright test tooling.** Add `@playwright/test` as a dev dependency compatible with the existing runtime; add `test:e2e: "playwright test"`. Configure Chromium first, fixed locale/timezone, `testDir: './tests/e2e'`, and base URL `http://127.0.0.1:4173`. The `webServer.command` is `pnpm dev --host 127.0.0.1 --port 4173 --strictPort` by default, or `pnpm preview --host 127.0.0.1 --port 4173 --strictPort` when `UI_PREVIEW=1`; disable server reuse for acceptance runs so a stale server cannot mask changed code. Set Vitest include to `tests/**/*.test.ts` and exclude `tests/e2e/**` so the two runners never collect each other's files. Install Chromium using `pnpm exec playwright install chromium`; on Linux install browser OS prerequisites where needed. Do not alter application source just to make a test runner start.
- [ ] **7.2 Create deterministic API fixtures.** Mock login/register JSON responses (both success and 401/error), `/api/workspace/conversations`, `/api/conversations`, and `/api/conversations/<id>/messages`. Seed two conversations, long-title/many-conversation variants, and user/assistant messages with fixed timestamps. Mock `/api/chat/<id>/messages` with correctly formatted SSE chunk/complete data. A fulfilled SSE fixture can verify final rendering; delayed progressive arrival is verified with a controlled ReadableStream in component/store tests rather than pretending a buffered response tests streaming timing. Set a test token before navigating to protected routes; never use a real credential.
- [ ] **7.3 Write a real computed-style smoke test.** On the login page, assert the submit button has at least **36px** height, nonzero horizontal padding, a non-transparent semantic background, a radius, and **14px** font size. Assert auth card padding is **32px** on desktop / **24px** mobile. On chat, assert expanded sidebar is **256px**, rows have **12px/8px** padding, icon/text gap is **12px**, and stream padding is **24px** desktop / **16px** mobile. Use `getComputedStyle` and bounding boxes, not just class attributes. Verify body margin is **0** from Preflight. These checks must fail if the CSS reverts to the v3 entry point.
- [ ] **7.4 Add browser responsive/focus tests.** Cover **1440×900**, **1024×768**, **768×1024**, **767×1024**, and **390×844**. Assert no page-level horizontal overflow (`scrollWidth <= clientWidth + 1`), visible composer bounds inside the viewport, and independent stream/sidebar scrolling with 100 messages and 50 conversations. Test collapse/reopen, mobile drawer open/Escape close/focus return, keyboard activation of nav and conversation rows, labels via `getByLabel`, and a visible focus ring. Repeat overflow checks with a 300-character title and 2000-character unbroken message.
- [ ] **7.5 Verify themes and icons as actual assets.** Test dark/light root classes, refresh persistence, status/form/message readability, and native controls' color scheme. Await `document.fonts.ready`; assert the declared PrimeIcons font is loaded and a used icon's `::before` has real nonempty/non-`none` content. Check there are no font/CSS 404s or application console errors. Do not use the unavailable `pi-anchor`. Confirm glyphs fit within buttons without clipped line-height or excess gap.
- [ ] **7.6 Exercise the authenticated user journey.** Register with confirmation, log out, log in, create a conversation, select another, send with Enter, render response, and show a mocked failure as an actionable alert. Include unsuccessful login staying on the form, loading/double-submit guards, Shift+Enter, and disabled streaming send. Verify no fake Running state when idle and no fake navigation/export/stop actions.
- [ ] **7.7 Capture and review screenshots before accepting baselines.** Capture login, register, empty chat, populated chat, error state, collapsed sidebar, and mobile drawer in dark/light mode. Use fixed data and disable animations for screenshot stability. Compare spacing/type hierarchy and control treatment with the pinned admin template and the visual contract. A human or browser-capable reviewer must inspect the first images; do not auto-approve whatever the application renders by blindly updating snapshots. Store only reviewed, deterministic baselines in the test tree; keep exploratory screenshots outside tracked source.
- [ ] **7.8 Document the exact developer workflow in `apps/web/README.md`.** Include install, dev/proxy, `pnpm typecheck`, `pnpm test:run`, `pnpm build`, browser install, `pnpm test:e2e`, and intentional baseline review/update commands. Explain Tailwind v4 imports/token mapping and PrimeIcons requirements so the original failure is not reintroduced. Do not change Docker/NGINX deployment architecture as incidental UI work; if production serving reveals a separate routing/proxy issue, report it separately.
- [ ] **7.9 Run final acceptance commands.** From `apps/web`, run `pnpm typecheck`, `pnpm test:run`, `pnpm build`, `pnpm test:e2e`, and `UI_PREVIEW=1 pnpm test:e2e`. The last command launches the preview server through Playwright and repeats the same CSS/font/journey checks against production assets using fixtures. Confirm dev and production both look styled. From the worktree root run `git diff --check` and inspect changed files for secrets/unrelated edits. Report failures rather than claiming completion from build success alone. Do not commit or push without authorization.

## 5. Acceptance Checklist

- [ ] Tailwind default utilities, Preflight, and semantic tokens are present in freshly generated CSS.
- [ ] No v3 `@tailwind` directives, wrong PrimeIcons imports, or duplicated global CSS entry points remain.
- [ ] Auth/card/sidebar/button/composer spacing matches the visual contract in actual computed styles.
- [ ] The app uses real local shadcn-vue primitives and a template-inspired responsive shell, not only installed packages.
- [ ] Semantic token styling works in dark and light modes, and preference persists.
- [ ] Every used PrimeIcons glyph exists, its font loads, and controls retain accessible names.
- [ ] Registration/login preserve validation, pending state, request-object contracts, and success-only navigation.
- [ ] Conversation history/new/select/logout/error flows work; no mock sessions/workspaces or dead controls are shown.
- [ ] Messages wrap, streaming has one assistant row, chunk updates follow appropriately, and manual history reading is not interrupted.
- [ ] Composer supports form submission, Enter, Shift+Enter, IME, blank/disabled guards, and multiline growth.
- [ ] Sidebar/stream scrolling and composer visibility work at desktop, breakpoint boundaries, and mobile widths.
- [ ] Unit tests, Vue typecheck, production build, and Playwright checks pass; screenshots receive an actual visual review.
- [ ] No real model calls, credentials, unexplained deleted tests, unrelated backend changes, commits, or pushes accompany this repair without authorization.

## 6. Planning Handoff

This document replaces the previous mockup-copy plan. The previous instructions were unsafe to follow because they repeated the wrong Tailwind version syntax, provided an invalid icon import, copied dead mock controls, and did not protect auth/chat behavior.

**Current state:** Review and planning only. The UI is not fixed yet. All implementation task checkboxes remain unchecked. Obtain authorization before Task 1; then execute in order and stop/report any mismatch between this plan, generated component contracts, and the installed toolchain.
