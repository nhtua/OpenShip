// src/main.ts
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { router } from './router'
import App from './App.vue'
import { useThemeStore } from './stores/theme'
import './assets/index.css'
import 'primeicons/primeicons.css'

const app = createApp(App)
const pinia = createPinia()
app.use(pinia)

// Apply the persisted theme (default: dark) before first paint.
useThemeStore(pinia).initializeTheme()

app.use(router)
app.mount('#app')
