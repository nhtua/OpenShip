<template>
  <div class="p-8 max-w-4xl mx-auto">
    <div class="mb-8">
      <h1 class="text-2xl font-bold text-gray-100 mb-2">New Project</h1>
      <p class="text-gray-400">Create a project to group workflow executions, resources, and tags</p>
    </div>

    <form @submit.prevent="createProject" class="space-y-6">
      <!-- Project Name -->
      <div>
        <label class="block text-sm font-medium text-gray-300 mb-2">Project Name <span class="text-red-400">*</span></label>
        <input v-model="form.name" type="text" required
          placeholder="e.g., web-platform"
          class="w-full bg-[#0d1117] border border-[#30363d] rounded px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-[#58a6ff]"/>
        <p class="mt-1 text-xs text-gray-500">A unique identifier for this project</p>
      </div>

      <!-- Description -->
      <div>
        <label class="block text-sm font-medium text-gray-300 mb-2">Description</label>
        <textarea v-model="form.description" rows="3"
          placeholder="Describe the purpose of this project..."
          class="w-full bg-[#0d1117] border border-[#30363d] rounded px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-[#58a6ff] resize-none"></textarea>
      </div>

      <!-- Enabled Connectors -->
      <div>
        <label class="block text-sm font-medium text-gray-300 mb-2">Enabled Connectors</label>
        <p class="mb-3 text-xs text-gray-500">Select the connectors this project can use for workflow execution</p>
        
        <div class="grid grid-cols-1 md:grid-cols-2 gap-3 max-h-64 overflow-y-auto border border-[#30363d] rounded p-4 bg-[#0d1117]">
          <label v-for="connector in connectors" :key="connector.id"
            class="flex items-center gap-3 p-2 rounded hover:bg-[#161b22] cursor-pointer">
            <input type="checkbox" :value="connector.id" v-model="form.connectors"
              class="rounded border-[#30363d] bg-[#0d1117] text-[#58a6ff] focus:ring-[#58a6ff]"/>
            <div class="flex-1 min-w-0">
              <span class="text-sm text-gray-100 block truncate">{{ connector.name }}</span>
              <span class="text-xs text-gray-500 block truncate">{{ connector.description }}</span>
            </div>
          </label>
        </div>
      </div>

      <!-- Environment Variables -->
      <div>
        <label class="block text-sm font-medium text-gray-300 mb-2">Environment Variables</label>
        <p class="mb-3 text-xs text-gray-500">Key-value pairs available to workflow executions in this project</p>
        
        <div v-for="(env, index) in form.env" :key="index" class="flex gap-2 mb-2">
          <input v-model="env.key" type="text" placeholder="KEY"
            class="flex-1 bg-[#0d1117] border border-[#30363d] rounded px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-[#58a6ff]"/>
          <input v-model="env.value" type="text" placeholder="value"
            class="flex-1 bg-[#0d1117] border border-[#30363d] rounded px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-[#58a6ff]"/>
          <button @click="removeEnv(index)" type="button"
            class="px-3 py-2 text-gray-500 hover:text-red-400 transition-colors">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
          </button>
        </div>
        
        <button @click="addEnv" type="button"
          class="text-sm text-[#58a6ff] hover:text-[#79c0ff] transition-colors">
          + Add environment variable
        </button>
      </div>

      <!-- Actions -->
      <div class="flex gap-3 pt-4 border-t border-[#30363d]">
        <button type="submit"
          class="bg-[#238636] hover:bg-[#2ea043] text-white px-4 py-2 rounded text-sm font-medium transition-colors">
          Create Project
        </button>
        <router-link to="/"
          class="text-sm text-gray-400 hover:text-gray-300 px-4 py-2 rounded border border-[#30363d] hover:border-[#484f58] transition-colors">
          Cancel
        </router-link>
      </div>
    </form>
  </div>
</template>

<script setup>
import { reactive } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()

const form = reactive({
  name: '',
  description: '',
  connectors: [],
  env: [{ key: '', value: '' }]
})

const connectors = [
  { id: 'aws', name: 'AWS', description: 'Amazon Web Services' },
  { id: 'gcp', name: 'GCP', description: 'Google Cloud Platform' },
  { id: 'azure', name: 'Azure', description: 'Microsoft Azure' },
  { id: 'kubernetes', name: 'Kubernetes', description: 'K8s cluster management' },
  { id: 'github', name: 'GitHub', description: 'Repository and CI integration' },
  { id: 'jira', name: 'Jira', description: 'Issue and project management' },
  { id: 'slack', name: 'Slack', description: 'Team communication' },
  { id: 'terraform', name: 'Terraform', description: 'Infrastructure as code' },
  { id: 'vault', name: 'Vault', description: 'Secrets management' },
  { id: 'prometheus', name: 'Prometheus', description: 'Monitoring and metrics' }
]

function addEnv() {
  form.env.push({ key: '', value: '' })
}

function removeEnv(index) {
  if (form.env.length > 1) {
    form.env.splice(index, 1)
  }
}

function createProject() {
  console.log('Creating project:', form)
  router.push('/')
}
</script>