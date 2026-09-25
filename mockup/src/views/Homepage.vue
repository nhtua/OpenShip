<template>
  <div class="p-8 max-w-6xl mx-auto">
    <!-- Hero Chat Input -->
    <div class="text-center mb-10">
      <h1 class="text-3xl font-bold text-gray-100 mb-2">What are you going to do?</h1>
      <p class="text-gray-400 mb-6">Describe your task and OpenShip will route you to the right workflow</p>
      <div class="relative max-w-2xl mx-auto">
        <input
          type="text"
          placeholder="Set up CI/CD for my Go service..."
          class="w-full bg-[#161b22] border border-[#30363d] rounded-lg px-5 py-4 text-gray-100 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-[#58a6ff] focus:border-transparent"
        />
        <button class="absolute right-3 top-1/2 -translate-y-1/2 bg-[#238636] hover:bg-[#2ea043] text-white px-4 py-2 rounded-md text-sm font-medium transition-colors">
          Start
        </button>
      </div>
    </div>

    <!-- Workflow Templates -->
    <div class="mb-10">
      <h2 class="text-lg font-semibold text-gray-200 mb-4">Built-in Workflows</h2>
      <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
        <router-link to="/infrastructure" class="group">
          <div class="bg-[#161b22] border border-[#30363d] rounded-lg p-5 hover:border-[#58a6ff] transition-all cursor-pointer">
            <div class="w-10 h-10 bg-blue-500/10 rounded-lg flex items-center justify-center mb-3">
              <svg class="w-5 h-5 text-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 12h14M5 12a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v4a2 2 0 01-2 2M5 12a2 2 0 00-2 2v4a2 2 0 002 2h14a2 2 0 002-2v-4a2 2 0 00-2-2"/></svg>
            </div>
            <h3 class="font-semibold text-gray-100 mb-1">Provision & Build</h3>
            <p class="text-sm text-gray-400">Describe your infrastructure, get Terraform code, diagrams, and cloud resources</p>
          </div>
        </router-link>

        <router-link to="/debug" class="group">
          <div class="bg-[#161b22] border border-[#30363d] rounded-lg p-5 hover:border-[#58a6ff] transition-all cursor-pointer">
            <div class="w-10 h-10 bg-purple-500/10 rounded-lg flex items-center justify-center mb-3">
              <svg class="w-5 h-5 text-purple-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg>
            </div>
            <h3 class="font-semibold text-gray-100 mb-1">Investigate & Debug</h3>
            <p class="text-sm text-gray-400">Gather telemetry, form hypotheses, test fixes, and propose solutions</p>
          </div>
        </router-link>

        <router-link to="/cicd" class="group">
          <div class="bg-[#161b22] border border-[#30363d] rounded-lg p-5 hover:border-[#58a6ff] transition-all cursor-pointer">
            <div class="w-10 h-10 bg-green-500/10 rounded-lg flex items-center justify-center mb-3">
              <svg class="w-5 h-5 text-green-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
            </div>
            <h3 class="font-semibold text-gray-100 mb-1">CI/CD Setup</h3>
            <p class="text-sm text-gray-400">Generate pipeline workflows, manage runs, and monitor builds</p>
          </div>
        </router-link>
      </div>
    </div>

    <!-- Custom Workflows -->
    <div class="mb-10">
      <div class="flex items-center justify-between mb-4">
        <h2 class="text-lg font-semibold text-gray-200">Custom Workflows</h2>
        <router-link to="/workflow-builder" class="text-sm text-[#58a6ff] hover:text-[#79b8ff] flex items-center gap-1">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/></svg>
          Build new workflow
        </router-link>
      </div>
      <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div class="bg-[#161b22] border border-[#30363d] rounded-lg p-5 hover:border-[#58a6ff] transition-all cursor-pointer">
          <div class="flex items-start justify-between">
            <div>
              <h3 class="font-semibold text-gray-100 mb-1">Deploy to staging</h3>
              <p class="text-sm text-gray-400">Run tests, build image, deploy to staging cluster</p>
            </div>
            <span class="bg-[#30363d] text-gray-400 text-xs px-2 py-1 rounded">v1.2</span>
          </div>
          <div class="flex gap-2 mt-3">
            <span class="bg-blue-500/10 text-blue-400 text-xs px-2 py-1 rounded">terraform</span>
            <span class="bg-green-500/10 text-green-400 text-xs px-2 py-1 rounded">github-actions</span>
          </div>
        </div>

        <div class="bg-[#161b22] border border-[#30363d] rounded-lg p-5 hover:border-[#58a6ff] transition-all cursor-pointer">
          <div class="flex items-start justify-between">
            <div>
              <h3 class="font-semibold text-gray-100 mb-1">Weekly cost report</h3>
              <p class="text-sm text-gray-400">Fetch cloud costs, analyze trends, post to Slack</p>
            </div>
            <span class="bg-[#30363d] text-gray-400 text-xs px-2 py-1 rounded">v0.9</span>
          </div>
          <div class="flex gap-2 mt-3">
            <span class="bg-blue-500/10 text-blue-400 text-xs px-2 py-1 rounded">aws</span>
            <span class="bg-purple-500/10 text-purple-400 text-xs px-2 py-1 rounded">slack</span>
          </div>
        </div>
      </div>
    </div>

    <!-- Recent Projects -->
    <div>
      <h2 class="text-lg font-semibold text-gray-200 mb-4">Recent Projects</h2>
      <div class="space-y-2">
        <div class="bg-[#161b22] border border-[#30363d] rounded-lg p-4 flex items-center justify-between hover:border-[#58a6ff] transition-all cursor-pointer">
          <div class="flex items-center gap-3">
            <div class="w-8 h-8 bg-[#30363d] rounded flex items-center justify-center">
              <span class="text-xs text-gray-400">WP</span>
            </div>
            <div>
              <p class="font-medium text-gray-100 text-sm">web-platform</p>
              <p class="text-xs text-gray-500">Last active 2 hours ago</p>
            </div>
          </div>
          <span class="text-xs text-green-400 bg-green-500/10 px-2 py-1 rounded">3 active workflows</span>
        </div>

        <div class="bg-[#161b22] border border-[#30363d] rounded-lg p-4 flex items-center justify-between hover:border-[#58a6ff] transition-all cursor-pointer">
          <div class="flex items-center gap-3">
            <div class="w-8 h-8 bg-[#30363d] rounded flex items-center justify-center">
              <span class="text-xs text-gray-400">SE</span>
            </div>
            <div>
              <p class="font-medium text-gray-100 text-sm">staging-env</p>
              <p class="text-xs text-gray-500">Last active yesterday</p>
            </div>
          </div>
          <span class="text-xs text-gray-400 bg-gray-500/10 px-2 py-1 rounded">idle</span>
        </div>

        <div class="bg-[#161b22] border border-[#30363d] rounded-lg p-4 flex items-center justify-between hover:border-[#58a6ff] transition-all cursor-pointer">
          <div class="flex items-center gap-3">
            <div class="w-8 h-8 bg-[#30363d] rounded flex items-center justify-center">
              <span class="text-xs text-gray-400">PI</span>
            </div>
            <div>
              <p class="font-medium text-gray-100 text-sm">prod-infra</p>
              <p class="text-xs text-gray-500">Last active 3 days ago</p>
            </div>
          </div>
          <span class="text-xs text-gray-400 bg-gray-500/10 px-2 py-1 rounded">idle</span>
        </div>
      </div>
    </div>
  </div>
</template>