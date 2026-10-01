import { createRouter, createWebHistory } from 'vue-router'
import HomeView from '../views/HomeView.vue'
import AgentWorkspace from '../views/AgentWorkspace.vue'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'home',
      component: HomeView
    },
    {
      path: '/workspace/:executionId?',
      name: 'agent-workspace',
      component: AgentWorkspace
    }
  ]
})