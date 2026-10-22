import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import type { Component } from 'vue'

export interface MountResult {
  wrapper: VueWrapper
  router: Router
  pinia: ReturnType<typeof createPinia>
}

/**
 * Mounts a view with a memory router that mirrors the app's real route
 * names (home, conversation, and auth pages) and a fresh pinia.
 */
export async function mountWithRouter(
  component: Component,
  initialPath: string = '/',
  props: Record<string, unknown> = {},
): Promise<MountResult> {
  const pinia = createPinia()
  setActivePinia(pinia)

  const routes = [
    { path: '/', name: 'chat', component: { template: '<div>Chat</div>' } },
    {
      path: '/conversation/:conversationId',
      name: 'conversation',
      component: { template: '<div>Chat</div>' },
      meta: { requiresAuth: true },
    },
    {
      path: '/settings/connectors',
      name: 'connectors-settings',
      component: { template: '<div>Connectors</div>' },
      meta: { requiresAuth: true },
    },
    { path: '/login', name: 'login', component: { template: '<div>Login</div>' } },
    {
      path: '/register',
      name: 'register',
      component: { template: '<div>Register</div>' },
    },
  ]

  const router = createRouter({
    history: createMemoryHistory(),
    routes,
  })

  await router.replace(initialPath)
  await router.isReady()

  const wrapper = mount(component, {
    props,
    global: {
      plugins: [router, pinia],
    },
  })

  return { wrapper, router, pinia }
}
