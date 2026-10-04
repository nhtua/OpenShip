import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryRouter, createRouter, type Router } from 'vue-router'
import type { Component, Ref } from 'vue'

export interface MountResult {
  wrapper: VueWrapper
  router: Router
  pinia: ReturnType<typeof createPinia>
}

export async function mountWithRouter(
  component: Component,
  initialPath: string = '/',
): Promise<MountResult> {
  const pinia = createPinia()
  setActivePinia(pinia)

  const routes = [
    { path: '/', name: 'login', component: { template: '<div>Redirect</div>' } },
    { path: '/login', name: 'login', component: { template: '<div>Login</div>' } },
    { path: '/register', name: 'register', component: { template: '<div>Register</div>' } },
    { path: '/chat', name: 'chat', component: { template: '<div>Chat</div>' } },
  ]

  const router = createRouter({
    history: createMemoryRouter().history,
    routes,
  })

  await router.replace(initialPath)
  await router.isReady()

  const wrapper = mount(component, {
    global: {
      plugins: [router, pinia],
    },
  })

  return { wrapper, router, pinia }
}
