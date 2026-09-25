<template>
  <div class="p-8 max-w-4xl mx-auto">
    <div class="mb-8">
      <h1 class="text-2xl font-bold text-gray-100 mb-2">New Project</h1>
      <p class="text-gray-400">Choose how to start your project</p>
    </div>

    <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
      <!-- Describe your project -->
      <div class="bg-[#161b22] border border-[#30363d] rounded-lg p-6">
        <div class="w-12 h-12 bg-blue-500/10 rounded-lg flex items-center justify-center mb-4">
          <svg class="w-6 h-6 text-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg>
        </div>
        <h3 class="text-lg font-semibold text-gray-100 mb-2">Describe your project</h3>
        <p class="text-sm text-gray-400 mb-4">Start with a text-based requirements document. Describe your infrastructure, components, and desired behavior in natural language.</p>
        
        <div class="space-y-3">
          <input type="text" placeholder="Project name" class="w-full bg-[#0d1117] border border-[#30363d] rounded px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-[#58a6ff]"/>
          <textarea placeholder="Describe your infrastructure...&#10;&#10;I need a web platform with:&#10;- API server running Go&#10;- PostgreSQL database&#10;- Redis cache&#10;- Load balanced across 3 instances" rows="5" class="w-full bg-[#0d1117] border border-[#30363d] rounded px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-[#58a6ff] resize-none"></textarea>
          <button class="w-full bg-[#238636] hover:bg-[#2ea043] text-white px-4 py-2 rounded text-sm font-medium transition-colors">
            Generate Design & Plan
          </button>
        </div>
      </div>

      <!-- Build with Diagram -->
      <div class="bg-[#161b22] border border-[#30363d] rounded-lg p-6">
        <div class="w-12 h-12 bg-purple-500/10 rounded-lg flex items-center justify-center mb-4">
          <svg class="w-6 h-6 text-purple-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 5a1 1 0 011-1h14a1 1 0 011 1v2a1 1 0 01-1 1H5a1 1 0 01-1-1V5zM4 11a1 1 0 011-1h6a1 1 0 011 1v2a1 1 0 01-1 1H5a1 1 0 01-1-1v-2zM17 11a1 1 0 011-1h1a1 1 0 011 1v2a1 1 0 01-1 1h-1a1 1 0 01-1-1v-2zM4 17a1 1 0 011-1h14a1 1 0 011 1v2a1 1 0 01-1 1H5a1 1 0 01-1-1v-2z"/></svg>
        </div>
        <h3 class="text-lg font-semibold text-gray-100 mb-2">Build with Diagram</h3>
        <p class="text-sm text-gray-400 mb-4">Sketch your architecture with diagram-as-code (Mermaid). The agent will refine it and generate Terraform from the design.</p>
        
        <div class="space-y-3">
          <input type="text" placeholder="Project name" class="w-full bg-[#0d1117] border border-[#30363d] rounded px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-[#58a6ff]"/>
          <div class="bg-[#0d1117] border border-[#30363d] rounded p-3">
            <pre class="text-xs text-gray-400 font-mono">graph TD
    LB[Load Balancer] --> S1[Server 1]
    LB --> S2[Server 2]
    S1 --> DB[(PostgreSQL)]
    S2 --> DB
    S1 --> R[(Redis)]
    S2 --> R</pre>
          </div>
          <button class="w-full bg-[#238636] hover:bg-[#2ea043] text-white px-4 py-2 rounded text-sm font-medium transition-colors">
            Refine & Generate Infrastructure
          </button>
        </div>
      </div>
    </div>
  </div>
</template>