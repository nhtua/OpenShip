<template>
  <div class="p-8 max-w-6xl mx-auto">
    <div class="mb-8">
      <h1 class="text-2xl font-bold text-gray-100 mb-2">Connectors</h1>
      <p class="text-gray-400">Manage authentication to third-party platforms and services</p>
    </div>

    <!-- Search -->
    <div class="mb-6">
      <input
        type="text"
        placeholder="Search connectors..."
        class="w-full bg-[#161b22] border border-[#30363d] rounded-lg px-4 py-3 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-[#58a6ff]"
      />
    </div>

    <!-- Connected -->
    <div class="mb-8">
      <h2 class="text-lg font-semibold text-gray-200 mb-4">Connected</h2>
      <div class="space-y-4">
        <div class="bg-[#161b22] border border-[#30363d] rounded-lg p-5">
          <div class="flex items-start justify-between">
            <div class="flex items-center gap-4">
              <div class="w-10 h-10 bg-orange-500/10 rounded-lg flex items-center justify-center">
                <span class="text-sm text-orange-400 font-bold">GH</span>
              </div>
              <div>
                <h3 class="font-semibold text-gray-100">GitHub</h3>
                <p class="text-sm text-gray-400">Connected via OAuth • org: web-platform-team</p>
              </div>
            </div>
            <div class="flex items-center gap-2">
              <span class="text-xs text-green-400 bg-green-500/10 px-2 py-1 rounded">active</span>
              <button class="bg-[#0d1117] border border-[#30363d] hover:border-[#58a6ff] text-gray-300 px-3 py-1.5 rounded text-sm transition-colors">
                Manage
              </button>
            </div>
          </div>
        </div>

        <div class="bg-[#161b22] border border-[#30363d] rounded-lg p-5">
          <div class="flex items-start justify-between">
            <div class="flex items-center gap-4">
              <div class="w-10 h-10 bg-green-500/10 rounded-lg flex items-center justify-center">
                <span class="text-sm text-green-400 font-bold">AWS</span>
              </div>
              <div>
                <h3 class="font-semibold text-gray-100">AWS</h3>
                <p class="text-sm text-gray-400">Connected via IAM role • account: 123456789</p>
              </div>
            </div>
            <div class="flex items-center gap-2">
              <span class="text-xs text-green-400 bg-green-500/10 px-2 py-1 rounded">active</span>
              <button class="bg-[#0d1117] border border-[#30363d] hover:border-[#58a6ff] text-gray-300 px-3 py-1.5 rounded text-sm transition-colors">
                Manage
              </button>
            </div>
          </div>
        </div>

        <div class="bg-[#161b22] border border-[#30363d] rounded-lg p-5">
          <div class="flex items-start justify-between">
            <div class="flex items-center gap-4">
              <div class="w-10 h-10 bg-purple-500/10 rounded-lg flex items-center justify-center">
                <span class="text-sm text-purple-400 font-bold">SL</span>
              </div>
              <div>
                <h3 class="font-semibold text-gray-100">Slack</h3>
                <p class="text-sm text-gray-400">Connected via OAuth • workspace: web-platform</p>
              </div>
            </div>
            <div class="flex items-center gap-2">
              <span class="text-xs text-green-400 bg-green-500/10 px-2 py-1 rounded">active</span>
              <button class="bg-[#0d1117] border border-[#30363d] hover:border-[#58a6ff] text-gray-300 px-3 py-1.5 rounded text-sm transition-colors">
                Manage
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Available -->
    <div>
      <h2 class="text-lg font-semibold text-gray-200 mb-4">Available</h2>
      <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div class="bg-[#161b22] border border-[#30363d] rounded-lg p-5">
          <div class="flex items-start justify-between mb-3">
            <div class="flex items-center gap-4">
              <div class="w-10 h-10 bg-blue-500/10 rounded-lg flex items-center justify-center">
                <span class="text-sm text-blue-400 font-bold">JIR</span>
              </div>
              <div>
                <h3 class="font-semibold text-gray-100">Jira</h3>
                <p class="text-sm text-gray-400">Manage issues, projects, and boards</p>
              </div>
            </div>
            <span class="text-xs text-gray-500 bg-[#30363d] px-2 py-1 rounded">not connected</span>
          </div>
          <button class="w-full bg-[#0d1117] border border-[#30363d] hover:border-[#58a6ff] text-gray-300 px-4 py-2 rounded text-sm transition-colors">
            Connect
          </button>
        </div>

        <div class="bg-[#161b22] border border-[#30363d] rounded-lg p-5">
          <div class="flex items-start justify-between mb-3">
            <div class="flex items-center gap-4">
              <div class="w-10 h-10 bg-green-500/10 rounded-lg flex items-center justify-center">
                <span class="text-sm text-green-400 font-bold">GCP</span>
              </div>
              <div>
                <h3 class="font-semibold text-gray-100">Google Cloud</h3>
                <p class="text-sm text-gray-400">GCP services via gcloud SDK</p>
              </div>
            </div>
            <span class="text-xs text-gray-500 bg-[#30363d] px-2 py-1 rounded">not connected</span>
          </div>
          <button class="w-full bg-[#0d1117] border border-[#30363d] hover:border-[#58a6ff] text-gray-300 px-4 py-2 rounded text-sm transition-colors">
            Connect
          </button>
        </div>

        <div class="bg-[#161b22] border border-[#30363d] rounded-lg p-5">
          <div class="flex items-start justify-between mb-3">
            <div class="flex items-center gap-4">
              <div class="w-10 h-10 bg-blue-500/10 rounded-lg flex items-center justify-center">
                <span class="text-sm text-blue-400 font-bold">AZ</span>
              </div>
              <div>
                <h3 class="font-semibold text-gray-100">Azure</h3>
                <p class="text-sm text-gray-400">Azure services via Azure CLI</p>
              </div>
            </div>
            <span class="text-xs text-gray-500 bg-[#30363d] px-2 py-1 rounded">not connected</span>
          </div>
          <button class="w-full bg-[#0d1117] border border-[#30363d] hover:border-[#58a6ff] text-gray-300 px-4 py-2 rounded text-sm transition-colors">
            Connect
          </button>
        </div>

        <div class="bg-[#161b22] border border-[#30363d] rounded-lg p-5">
          <div class="flex items-start justify-between mb-3">
            <div class="flex items-center gap-4">
              <div class="w-10 h-10 bg-red-500/10 rounded-lg flex items-center justify-center">
                <span class="text-sm text-red-400 font-bold">K8s</span>
              </div>
              <div>
                <h3 class="font-semibold text-gray-100">Kubernetes</h3>
                <p class="text-sm text-gray-400">Cluster management via kubectl</p>
              </div>
            </div>
            <span class="text-xs text-gray-500 bg-[#30363d] px-2 py-1 rounded">not connected</span>
          </div>
          <button class="w-full bg-[#0d1117] border border-[#30363d] hover:border-[#58a6ff] text-gray-300 px-4 py-2 rounded text-sm transition-colors">
            Connect
          </button>
        </div>
      </div>
    </div>
  </div>
</template>