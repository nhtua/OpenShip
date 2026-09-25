import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'
import './index.css'

import Homepage from './views/Homepage.vue'
import NewProject from './views/NewProject.vue'
import AgentWorkspace from './views/AgentWorkspace.vue'
import ArtifactInspector from './views/ArtifactInspector.vue'
import WorkflowBuilder from './views/WorkflowBuilder.vue'
import ToolRegistry from './views/ToolRegistry.vue'
import Connectors from './views/Connectors.vue'
import InfrastructureWorkflow from './views/InfrastructureWorkflow.vue'
import CicdWorkflow from './views/CicdWorkflow.vue'
import DebugWorkflow from './views/DebugWorkflow.vue'

const routes = [
  { path: '/', component: Homepage },
  { path: '/new-project', component: NewProject },
  { path: '/agent-workspace', component: AgentWorkspace },
  { path: '/artifact-inspector', component: ArtifactInspector },
  { path: '/workflow-builder', component: WorkflowBuilder },
  { path: '/tool-registry', component: ToolRegistry },
  { path: '/connectors', component: Connectors },
  { path: '/infrastructure', component: InfrastructureWorkflow },
  { path: '/cicd', component: CicdWorkflow },
  { path: '/debug', component: DebugWorkflow },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

createApp(App).use(router).mount('#app')