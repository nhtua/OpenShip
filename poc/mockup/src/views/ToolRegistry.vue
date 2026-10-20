<template>
  <div class="p-8 max-w-6xl mx-auto">
    <div class="mb-8">
      <h1 class="text-2xl font-bold text-gray-100 mb-2">Tool Registry</h1>
      <p class="text-gray-400">Browse, search, and manage available tools and connectors</p>
    </div>

    <!-- Search & Filter -->
    <div class="flex gap-4 mb-6">
      <input
        type="text"
        placeholder="Search tools..."
        class="flex-1 bg-[#161b22] border border-[#30363d] rounded-lg px-4 py-3 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-[#58a6ff]"
      />
      <select class="bg-[#161b22] border border-[#30363d] rounded-lg px-4 py-3 text-sm text-gray-300 focus:outline-none">
        <option>All Types</option>
        <option>Tools</option>
        <option>Connectors (MCP)</option>
        <option>Custom Scripts</option>
      </select>
      <select class="bg-[#161b22] border border-[#30363d] rounded-lg px-4 py-3 text-sm text-gray-300 focus:outline-none">
        <option>All Status</option>
        <option>Built-in</option>
        <option>Community</option>
        <option>Custom</option>
      </select>
    </div>

    <!-- Built-in Tools -->
    <div class="mb-8">
      <h2 class="text-lg font-semibold text-gray-200 mb-4">Built-in Tools</h2>
      <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div class="bg-[#161b22] border border-[#30363d] rounded-lg p-5">
          <div class="flex items-start justify-between mb-3">
            <div class="flex items-center gap-3">
              <div class="w-8 h-8 bg-blue-500/10 rounded flex items-center justify-center">
                <svg class="w-4 h-4 text-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"/></svg>
              </div>
              <div>
                <h3 class="font-semibold text-gray-100 text-sm">terraform</h3>
                <p class="text-xs text-gray-500">terraform.apply, terraform.plan, terraform.destroy</p>
              </div>
            </div>
            <span class="text-xs text-gray-500 bg-[#30363d] px-2 py-1 rounded">built-in</span>
          </div>
          <p class="text-xs text-gray-400">Manage infrastructure as code with Terraform. Plan, apply, and destroy resources.</p>
        </div>

        <div class="bg-[#161b22] border border-[#30363d] rounded-lg p-5">
          <div class="flex items-start justify-between mb-3">
            <div class="flex items-center gap-3">
              <div class="w-8 h-8 bg-green-500/10 rounded flex items-center justify-center">
                <svg class="w-4 h-4 text-green-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"/></svg>
              </div>
              <div>
                <h3 class="font-semibold text-gray-100 text-sm">chat</h3>
                <p class="text-xs text-gray-500">chat.send, chat.ask</p>
              </div>
            </div>
            <span class="text-xs text-gray-500 bg-[#30363d] px-2 py-1 rounded">built-in</span>
          </div>
          <p class="text-xs text-gray-400">Send messages and ask questions through the chat interface.</p>
        </div>

        <div class="bg-[#161b22] border border-[#30363d] rounded-lg p-5">
          <div class="flex items-start justify-between mb-3">
            <div class="flex items-center gap-3">
              <div class="w-8 h-8 bg-purple-500/10 rounded flex items-center justify-center">
                <svg class="w-4 h-4 text-purple-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"/></svg>
              </div>
              <div>
                <h3 class="font-semibold text-gray-100 text-sm">file</h3>
                <p class="text-xs text-gray-500">file.read, file.write, file.list</p>
              </div>
            </div>
            <span class="text-xs text-gray-500 bg-[#30363d] px-2 py-1 rounded">built-in</span>
          </div>
          <p class="text-xs text-gray-400">Read, write, and list files in the sandbox working directory.</p>
        </div>
      </div>
    </div>

    <!-- Connectors (MCP) -->
    <div class="mb-8">
      <h2 class="text-lg font-semibold text-gray-200 mb-4">Connectors (MCP)</h2>
      <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div class="bg-[#161b22] border border-[#30363d] rounded-lg p-5">
          <div class="flex items-start justify-between mb-3">
            <div class="flex items-center gap-3">
              <div class="w-8 h-8 bg-orange-500/10 rounded flex items-center justify-center">
                <span class="text-xs text-orange-400 font-bold">GH</span>
              </div>
              <div>
                <h3 class="font-semibold text-gray-100 text-sm">github</h3>
                <p class="text-xs text-gray-500">issues, pulls, repos, actions</p>
              </div>
            </div>
            <span class="text-xs text-green-400 bg-green-500/10 px-2 py-1 rounded">connected</span>
          </div>
          <p class="text-xs text-gray-400">Interact with GitHub repositories, issues, pull requests, and Actions.</p>
        </div>

        <div class="bg-[#161b22] border border-[#30363d] rounded-lg p-5">
          <div class="flex items-start justify-between mb-3">
            <div class="flex items-center gap-3">
              <div class="w-8 h-8 bg-blue-500/10 rounded flex items-center justify-center">
                <span class="text-xs text-blue-400 font-bold">JIR</span>
              </div>
              <div>
                <h3 class="font-semibold text-gray-100 text-sm">jira</h3>
                <p class="text-xs text-gray-500">issues, projects, boards</p>
              </div>
            </div>
            <span class="text-xs text-yellow-400 bg-yellow-500/10 px-2 py-1 rounded">not connected</span>
          </div>
          <p class="text-xs text-gray-400">Manage Jira issues, projects, and boards.</p>
        </div>

        <div class="bg-[#161b22] border border-[#30363d] rounded-lg p-5">
          <div class="flex items-start justify-between mb-3">
            <div class="flex items-center gap-3">
              <div class="w-8 h-8 bg-purple-500/10 rounded flex items-center justify-center">
                <span class="text-xs text-purple-400 font-bold">SL</span>
              </div>
              <div>
                <h3 class="font-semibold text-gray-100 text-sm">slack</h3>
                <p class="text-xs text-gray-500">messages, channels, users</p>
              </div>
            </div>
            <span class="text-xs text-green-400 bg-green-500/10 px-2 py-1 rounded">connected</span>
          </div>
          <p class="text-xs text-gray-400">Post messages to Slack channels and DMs.</p>
        </div>

        <div class="bg-[#161b22] border border-[#30363d] rounded-lg p-5">
          <div class="flex items-start justify-between mb-3">
            <div class="flex items-center gap-3">
              <div class="w-8 h-8 bg-green-500/10 rounded flex items-center justify-center">
                <span class="text-xs text-green-400 font-bold">AWS</span>
              </div>
              <div>
                <h3 class="font-semibold text-gray-100 text-sm">aws</h3>
                <p class="text-xs text-gray-500">EC2, S3, RDS, ECS, and more</p>
              </div>
            </div>
            <span class="text-xs text-green-400 bg-green-500/10 px-2 py-1 rounded">connected</span>
          </div>
          <p class="text-xs text-gray-400">Interact with AWS services via SDK.</p>
        </div>
      </div>
    </div>

    <!-- Custom Scripts -->
    <div>
      <h2 class="text-lg font-semibold text-gray-200 mb-4">Custom Scripts</h2>
      <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div class="bg-[#161b22] border border-[#30363d] rounded-lg p-5">
          <div class="flex items-start justify-between mb-3">
            <div class="flex items-center gap-3">
              <div class="w-8 h-8 bg-gray-500/10 rounded flex items-center justify-center">
                <span class="text-xs text-gray-400 font-bold">SH</span>
              </div>
              <div>
                <h3 class="font-semibold text-gray-100 text-sm">deploy-canary.sh</h3>
                <p class="text-xs text-gray-500">shell script</p>
              </div>
            </div>
            <span class="text-xs text-gray-500 bg-[#30363d] px-2 py-1 rounded">custom</span>
          </div>
          <p class="text-xs text-gray-400">Deploy canary release to production cluster.</p>
        </div>

        <div class="bg-[#161b22] border border-[#30363d] rounded-lg p-5">
          <div class="flex items-start justify-between mb-3">
            <div class="flex items-center gap-3">
              <div class="w-8 h-8 bg-blue-500/10 rounded flex items-center justify-center">
                <span class="text-xs text-blue-400 font-bold">PY</span>
              </div>
              <div>
                <h3 class="font-semibold text-gray-100 text-sm">rotate-secrets.py</h3>
                <p class="text-xs text-gray-500">Python script</p>
              </div>
            </div>
            <span class="text-xs text-gray-500 bg-[#30363d] px-2 py-1 rounded">custom</span>
          </div>
          <p class="text-xs text-gray-400">Rotate all secrets in HashiCorp Vault.</p>
        </div>
      </div>
    </div>
  </div>
</template>