<template>
  <div class="h-full flex flex-col">
    <!-- Header -->
    <div class="border-b border-[#30363d] px-6 py-4 bg-[#161b22]">
      <div class="flex items-center justify-between">
        <div>
          <h1 class="text-lg font-semibold text-gray-100">Investigate & Debug</h1>
          <p class="text-sm text-gray-400">web-platform • Session #1045</p>
        </div>
        <div class="flex items-center gap-3">
          <span class="flex items-center gap-2 text-sm text-yellow-400">
            <span class="w-2 h-2 bg-yellow-400 rounded-full animate-pulse"></span>
            Gathering telemetry
          </span>
          <button class="bg-[#0d1117] border border-[#30363d] hover:border-[#58a6ff] text-gray-300 px-3 py-1.5 rounded text-sm transition-colors">
            Export
          </button>
        </div>
      </div>
    </div>

    <!-- Chat Stream -->
    <div class="flex-1 overflow-y-auto p-6 space-y-4">
      <!-- User message -->
      <div class="flex justify-end">
        <div class="max-w-2xl bg-[#238636]/20 border border-[#238636]/30 rounded-lg px-4 py-3">
          <p class="text-sm text-gray-100">Users are reporting slow API responses, sometimes timing out after 30 seconds. This started about an hour ago.</p>
        </div>
      </div>

      <!-- Agent: start investigation -->
      <div class="flex gap-3">
        <div class="w-8 h-8 bg-[#58a6ff] rounded-lg flex items-center justify-center flex-shrink-0">
          <span class="text-white text-xs font-bold">AI</span>
        </div>
        <div class="max-w-2xl">
          <div class="bg-[#161b22] border border-[#30363d] rounded-lg px-4 py-3">
            <p class="text-sm text-gray-100">I'll investigate the slow API responses. Let me gather telemetry data from your monitoring systems.</p>
          </div>
        </div>
      </div>

      <!-- Telemetry gathering -->
      <div class="flex gap-3">
        <div class="w-8 h-8 bg-[#58a6ff] rounded-lg flex items-center justify-center flex-shrink-0">
          <span class="text-white text-xs font-bold">AI</span>
        </div>
        <div class="max-w-2xl space-y-3">
          <div class="bg-[#161b22] border border-[#30363d] rounded-lg overflow-hidden">
            <div class="bg-[#0d1117] px-4 py-2 flex items-center gap-2 border-b border-[#30363d]">
              <svg class="w-4 h-4 text-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/></svg>
              <span class="text-xs text-gray-400">Telemetry Data</span>
            </div>
            <div class="p-4 space-y-3">
              <div>
                <p class="text-xs text-gray-500 mb-1">API Response Time (avg)</p>
                <p class="text-sm text-red-400 font-mono">28.4s ↑ 470%</p>
              </div>
              <div>
                <p class="text-xs text-gray-500 mb-1">Error Rate (5xx)</p>
                <p class="text-sm text-yellow-400 font-mono">12.3% ↑ 8%</p>
              </div>
              <div>
                <p class="text-xs text-gray-500 mb-1">Database Queries (slow)</p>
                <p class="text-sm text-red-400 font-mono">847 ↑ 12x</p>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Hypothesis -->
      <div class="flex gap-3">
        <div class="w-8 h-8 bg-[#58a6ff] rounded-lg flex items-center justify-center flex-shrink-0">
          <span class="text-white text-xs font-bold">AI</span>
        </div>
        <div class="max-w-2xl space-y-3">
          <div class="bg-[#161b22] border border-[#30363d] rounded-lg px-4 py-3">
            <p class="text-sm text-gray-100">Based on the telemetry, I have a hypothesis: <strong>database query performance has degraded significantly</strong>, likely due to a missing index or a slow query introduced recently.</p>
          </div>
          <div class="bg-[#161b22] border border-[#30363d] rounded-lg overflow-hidden">
            <div class="bg-[#0d1117] px-4 py-2 flex items-center gap-2 border-b border-[#30363d]">
              <svg class="w-4 h-4 text-green-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg>
              <span class="text-xs text-gray-400">Testing Hypothesis</span>
            </div>
            <div class="p-4">
              <p class="text-xs text-gray-300 font-mono">Querying slow query log...</p>
              <div class="mt-3 bg-[#0d1117] rounded p-3 font-mono text-xs text-gray-300">
                <p>Slowest query: SELECT * FROM orders WHERE user_id = ? AND status = 'pending' ORDER BY created_at DESC LIMIT 100</p>
                <p class="text-red-400">Duration: 24.7s (no index on status column)</p>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Fix proposal -->
      <div class="flex gap-3">
        <div class="w-8 h-8 bg-[#58a6ff] rounded-lg flex items-center justify-center flex-shrink-0">
          <span class="text-white text-xs font-bold">AI</span>
        </div>
        <div class="max-w-2xl space-y-3">
          <div class="bg-[#161b22] border border-[#30363d] rounded-lg px-4 py-3">
            <p class="text-sm text-gray-100">The hypothesis is confirmed. Here's the fix:</p>
          </div>
          <div class="bg-[#161b22] border border-[#30363d] rounded-lg overflow-hidden">
            <div class="bg-[#0d1117] px-4 py-2 flex items-center justify-between border-b border-[#30363d]">
              <span class="text-xs text-gray-400">Proposed Fix</span>
              <span class="text-xs text-green-400 bg-green-500/10 px-2 py-0.5 rounded">ready</span>
            </div>
            <div class="p-4">
              <p class="text-sm text-gray-100 mb-3">Add a composite index on the orders table:</p>
              <div class="bg-[#0d1117] rounded p-3 font-mono text-xs text-gray-300 mb-4">
                CREATE INDEX idx_orders_user_status ON orders(user_id, status);
              </div>
              <p class="text-sm text-gray-300 mb-4">Expected result: Query time should drop from ~25s to &lt;100ms.</p>
              <div class="flex gap-2">
                <button class="bg-[#238636] hover:bg-[#2ea043] text-white px-4 py-2 rounded text-sm font-medium transition-colors">
                  Apply Fix
                </button>
                <button class="bg-[#0d1117] border border-[#30363d] hover:border-[#58a6ff] text-gray-300 px-4 py-2 rounded text-sm transition-colors">
                  Review Query
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Chat input -->
    <div class="border-t border-[#30363d] p-4 bg-[#161b22]">
      <div class="flex gap-3">
        <input
          type="text"
          placeholder="Ask about the investigation, approve fix, or suggest alternatives..."
          class="flex-1 bg-[#0d1117] border border-[#30363d] rounded-lg px-4 py-3 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-[#58a6ff]"
        />
        <button class="bg-[#238636] hover:bg-[#2ea043] text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors">
          Send
        </button>
      </div>
    </div>
  </div>
</template>