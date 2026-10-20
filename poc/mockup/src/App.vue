<template>
  <div class="flex h-screen">
    <aside class="w-64 bg-[#161b22] border-r border-[#30363d] flex flex-col">
      <div class="p-4 border-b border-[#30363d]">
        <router-link to="/" class="flex items-center gap-2">
          <div class="w-8 h-8 bg-[#58a6ff] rounded-lg flex items-center justify-center">
            <span class="text-white font-bold text-sm">OS</span>
          </div>
          <span class="font-semibold text-gray-100">OpenShip</span>
        </router-link>
      </div>
      
      <div class="p-3">
        <select class="w-full bg-[#0d1117] border border-[#30363d] rounded px-3 py-2 text-sm text-gray-300">
          <option>web-platform</option>
          <option>staging-env</option>
          <option>prod-infra</option>
        </select>
      </div>

      <nav class="flex-1 p-2 space-y-1">
        <router-link to="/" class="flex items-center gap-2 px-3 py-2 rounded text-sm text-gray-400 hover:bg-[#0d1117] hover:text-gray-200" :class="{'bg-[#0d1117] text-gray-100': $route.path === '/'}">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6"/></svg>
          Home
        </router-link>
        <router-link to="/new-project" class="flex items-center gap-2 px-3 py-2 rounded text-sm text-gray-400 hover:bg-[#0d1117] hover:text-gray-200" :class="{'bg-[#0d1117] text-gray-100': $route.path === '/new-project'}">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/></svg>
          New Project
        </router-link>
        <router-link to="/agent-workspace" class="flex items-center gap-2 px-3 py-2 rounded text-sm text-gray-400 hover:bg-[#0d1117] hover:text-gray-200" :class="{'bg-[#0d1117] text-gray-100': $route.path === '/agent-workspace'}">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z"/></svg>
          Agent Workspace
        </router-link>
        <router-link to="/workflow-builder" class="flex items-center gap-2 px-3 py-2 rounded text-sm text-gray-400 hover:bg-[#0d1117] hover:text-gray-200" :class="{'bg-[#0d1117] text-gray-100': $route.path === '/workflow-builder'}">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 5a1 1 0 011-1h14a1 1 0 011 1v2a1 1 0 01-1 1H5a1 1 0 01-1-1V5zM4 11a1 1 0 011-1h6a1 1 0 011 1v2a1 1 0 01-1 1H5a1 1 0 01-1-1v-2zM17 11a1 1 0 011-1h1a1 1 0 011 1v2a1 1 0 01-1 1h-1a1 1 0 01-1-1v-2zM4 17a1 1 0 011-1h14a1 1 0 011 1v2a1 1 0 01-1 1H5a1 1 0 01-1-1v-2z"/></svg>
          Workflow Builder
        </router-link>
        <router-link to="/tool-registry" class="flex items-center gap-2 px-3 py-2 rounded text-sm text-gray-400 hover:bg-[#0d1117] hover:text-gray-200" :class="{'bg-[#0d1117] text-gray-100': $route.path === '/tool-registry'}">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"/></svg>
          Tool Registry
        </router-link>
        <router-link to="/connectors" class="flex items-center gap-2 px-3 py-2 rounded text-sm text-gray-400 hover:bg-[#0d1117] hover:text-gray-200" :class="{'bg-[#0d1117] text-gray-100': $route.path === '/connectors'}">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1"/></svg>
          Connectors
        </router-link>
      </nav>

      <div class="p-3 border-t border-[#30363d]">
        <button class="w-full flex items-center gap-2 px-3 py-2 rounded text-sm text-gray-400 hover:bg-[#0d1117] hover:text-gray-200">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/></svg>
          Settings
        </button>
      </div>
    </aside>

    <main class="flex-1 overflow-auto">
      <router-view />
    </main>
  </div>
</template>