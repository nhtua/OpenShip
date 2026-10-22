import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { authReturnPath } from '@/lib/navigation'

const LoginView = () => import('./views/LoginView.vue')
const RegisterView = () => import('./views/RegisterView.vue')
const ChatView = () => import('./views/ChatView.vue')
const ConnectorSettingsView = () => import('./views/ConnectorSettingsView.vue')

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: LoginView,
    },
    {
      path: '/register',
      name: 'register',
      component: RegisterView,
    },
    {
      path: '/',
      name: 'chat',
      component: ChatView,
      meta: { requiresAuth: true },
    },
    {
      path: '/conversation/:conversationId',
      name: 'conversation',
      component: ChatView,
      meta: { requiresAuth: true },
    },
    {
      path: '/settings/connectors',
      name: 'connectors-settings',
      component: ConnectorSettingsView,
      meta: { requiresAuth: true },
    },
  ],
})

router.beforeEach((to) => {
  const auth = useAuthStore()
  if (to.meta.requiresAuth && !auth.token) {
    return {
      name: 'login',
      query: to.fullPath === '/' ? {} : { redirect: to.fullPath },
    }
  }
  if ((to.name === 'login' || to.name === 'register') && auth.token) {
    return authReturnPath(router, to.query.redirect)
  }
})
