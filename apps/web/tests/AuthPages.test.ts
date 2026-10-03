import { nextTick } from 'vue'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import LoginView from '@/views/LoginView.vue'
import RegisterView from '@/views/RegisterView.vue'
import ChatView from '@/views/ChatView.vue'
import { useAuthStore } from '@/stores/auth'

// Minimal router for testing
function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/login', name: 'login', component: { template: '<div>Login</div>' } },
      { path: '/register', name: 'register', component: { template: '<div>Register</div>' } },
      { path: '/', name: 'chat', component: { template: '<div>Chat</div>' } },
    ],
  })
}

const createLoginWrapper = () => {
  setActivePinia(createPinia())
  return mount(LoginView, { global: { plugins: [createTestRouter()] } })
}

describe('LoginView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  test('renders form', () => {
    const wrapper = createLoginWrapper()
    expect(wrapper.find('form').exists()).toBe(true)
    expect(wrapper.find('input[name="username"]').exists()).toBe(true)
    expect(wrapper.find('input[name="password"]').exists()).toBe(true)
  })

  test('displays sign in heading', () => {
    const wrapper = createLoginWrapper()
    expect(wrapper.find('h2').text()).toBe('Sign In')
  })

  test('has register link', () => {
    const wrapper = createLoginWrapper()
    expect(wrapper.find('a[href="/register"]').exists()).toBe(true)
  })

  test('submit button is disabled while loading', async () => {
    const wrapper = createLoginWrapper()
    const auth = useAuthStore()
    auth.isLoading = true
    await nextTick()
    const btn = wrapper.find('button[type="submit"]')
    expect(btn.element.disabled).toBe(true)
  })

  test('shows error message on login failure', async () => {
    const wrapper = createLoginWrapper()
    const auth = useAuthStore()
    auth.error = 'Invalid credentials'
    await nextTick()
    const alert = wrapper.find('[class*="text-destructive"]')
    expect(alert.exists()).toBe(true)
    expect(alert.text()).toContain('Invalid credentials')
  })
})

describe('RegisterView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  test('renders form', () => {
    const wrapper = mount(RegisterView, {
      global: { plugins: [createTestRouter()] },
    })
    expect(wrapper.find('form').exists()).toBe(true)
    expect(wrapper.find('input[name="username"]').exists()).toBe(true)
    expect(wrapper.find('input[name="email"]').exists()).toBe(true)
    expect(wrapper.find('input[name="password"]').exists()).toBe(true)
    expect(wrapper.find('input[name="confirmPassword"]').exists()).toBe(true)
  })

  test('displays create account heading', () => {
    const wrapper = mount(RegisterView, {
      global: { plugins: [createTestRouter()] },
    })
    expect(wrapper.find('h2').text()).toBe('Create Account')
  })

  test('has sign in link', () => {
    const wrapper = mount(RegisterView, {
      global: { plugins: [createTestRouter()] },
    })
    expect(wrapper.find('a[href="/login"]').exists()).toBe(true)
  })

  test('validation rejects short password', async () => {
    const wrapper = mount(RegisterView, {
      global: { plugins: [createTestRouter()] },
    })
    await wrapper.find('input[name="username"]').setValue('testuser')
    await wrapper.find('input[name="email"]').setValue('test@example.com')
    await wrapper.find('input[name="password"]').setValue('short')
    await wrapper.find('input[name="confirmPassword"]').setValue('short')
    await wrapper.find('form').trigger('submit')
    await nextTick()
    const alert = wrapper.find('[class*="text-destructive"]')
    expect(alert.exists()).toBe(true)
    expect(alert.text()).toContain('at least 8 characters')
  })

  test('validation rejects mismatched passwords', async () => {
    const wrapper = mount(RegisterView, {
      global: { plugins: [createTestRouter()] },
    })
    await wrapper.find('input[name="username"]').setValue('testuser')
    await wrapper.find('input[name="email"]').setValue('test@example.com')
    await wrapper.find('input[name="password"]').setValue('password123')
    await wrapper.find('input[name="confirmPassword"]').setValue('different1')
    await wrapper.find('form').trigger('submit')
    await nextTick()
    const alert = wrapper.find('[class*="text-destructive"]')
    expect(alert.exists()).toBe(true)
    expect(alert.text()).toContain('do not match')
  })
})

describe('ChatView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  test('renders chat heading', () => {
    const wrapper = mount(ChatView)
    expect(wrapper.find('h2').text()).toBe('Chat')
  })

  test('shows logout button when user is logged in', () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const auth = useAuthStore()
    auth.user = { id: '1', username: 'testuser', email: 'test@example.com' }
    auth.token = 'test-token'
    const wrapper = mount(ChatView)
    expect(wrapper.find('button').text()).toContain('Sign Out')
    expect(wrapper.find('button').text()).toContain('testuser')
  })
})

describe('Auth Store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  test('initial state has no user', () => {
    const auth = useAuthStore()
    expect(auth.user).toBeNull()
    expect(auth.token).toBeNull()
  })

  test('logout clears state and localStorage', () => {
    const auth = useAuthStore()
    auth.token = 'some-token'
    auth.user = { id: '1', username: 'testuser', email: 'test@example.com' }
    auth.logout()
    expect(auth.token).toBeNull()
    expect(auth.user).toBeNull()
    expect(localStorage.getItem('access_token')).toBeNull()
  })
})
