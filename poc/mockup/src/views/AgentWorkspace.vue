<template>
  <div class="h-full flex flex-col">
    <!-- Header -->
    <div class="border-b border-[#30363d] px-6 py-4 bg-[#161b22]">
      <div class="flex items-center justify-between">
        <div>
          <h1 class="text-lg font-semibold text-gray-100">Provision & Build</h1>
          <p class="text-sm text-gray-400">web-platform • Session #1042</p>
        </div>
        <div class="flex items-center gap-3">
          <span class="flex items-center gap-2 text-sm text-green-400">
            <span class="w-2 h-2 bg-green-400 rounded-full animate-pulse"></span>
            Running
          </span>
          <button class="bg-[#0d1117] border border-[#30363d] hover:border-[#58a6ff] text-gray-300 px-3 py-1.5 rounded text-sm transition-colors">
            Export
          </button>
          <button class="bg-[#da3633] hover:bg-[#f85149] text-white px-3 py-1.5 rounded text-sm transition-colors">
            Stop
          </button>
        </div>
      </div>
    </div>

    <!-- Chat Stream -->
    <div class="flex-1 overflow-y-auto p-6 space-y-4">
      <!-- User message -->
      <div class="flex justify-end">
        <div class="max-w-2xl bg-[#238636]/20 border border-[#238636]/30 rounded-lg px-4 py-3">
          <p class="text-sm text-gray-100">I need to set up a production web platform with an API server, PostgreSQL database, and Redis cache. The API should be load balanced across multiple instances.</p>
        </div>
      </div>

      <!-- Agent response -->
      <div class="flex gap-3">
        <div class="w-8 h-8 bg-[#58a6ff] rounded-lg flex items-center justify-center flex-shrink-0">
          <span class="text-white text-xs font-bold">AI</span>
        </div>
        <div class="max-w-2xl space-y-3">
          <div class="bg-[#161b22] border border-[#30363d] rounded-lg px-4 py-3">
            <p class="text-sm text-gray-100">I'll design a production-ready platform for you. Let me start by creating the architecture plan.</p>
          </div>

          <!-- Reasoning block -->
          <div class="bg-[#161b22] border border-[#30363d] rounded-lg overflow-hidden">
            <div class="bg-[#0d1117] px-4 py-2 flex items-center gap-2 border-b border-[#30363d]">
              <svg class="w-4 h-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"/></svg>
              <span class="text-xs text-gray-400">Reasoning</span>
            </div>
            <div class="p-4">
              <p class="text-sm text-gray-300">For a production web platform, I need to consider:</p>
              <ul class="text-sm text-gray-300 list-disc list-inside mt-2 space-y-1">
                <li>High availability via load balancing across 3+ instances</li>
                <li>Managed PostgreSQL for reliability and automatic backups</li>
                <li>Redis cluster for caching and session management</li>
                <li>VPC networking with proper security groups</li>
                <li>Health checks and auto-scaling</li>
              </ul>
            </div>
          </div>

          <!-- Tool call card -->
          <div class="bg-[#161b22] border border-[#30363d] rounded-lg overflow-hidden">
            <div class="bg-[#0d1117] px-4 py-2 flex items-center justify-between border-b border-[#30363d]">
              <div class="flex items-center gap-2">
                <svg class="w-4 h-4 text-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"/></svg>
                <span class="text-xs text-gray-400">Tool Call</span>
              </div>
              <span class="text-xs text-green-400 bg-green-500/10 px-2 py-0.5 rounded">completed</span>
            </div>
            <div class="p-4">
              <p class="text-sm font-medium text-gray-100 mb-2">terraform.plan</p>
              <div class="bg-[#0d1117] rounded p-3 font-mono text-xs text-gray-300">
                <p>+ aws_alb.web</p>
                <p>+ aws_alb_target_group.api</p>
                <p>+ aws_autoscaling_group.api (min: 2, max: 6)</p>
                <p>+ aws_rds_cluster.postgres</p>
                <p>+ aws_elasticache_cluster.redis</p>
                <p>+ aws_vpc.main</p>
                <p class="text-green-400 mt-2">Plan: 6 to add, 0 to change, 0 to destroy.</p>
              </div>
            </div>
          </div>

          <!-- Embedded artifact: architecture diagram card -->
          <div class="bg-[#161b22] border border-[#30363d] rounded-lg overflow-hidden">
            <div class="bg-[#0d1117] px-4 py-2 flex items-center justify-between border-b border-[#30363d]">
              <div class="flex items-center gap-2">
                <svg class="w-4 h-4 text-purple-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 5a1 1 0 011-1h14a1 1 0 011 1v2a1 1 0 01-1 1H5a1 1 0 01-1-1V5zM4 11a1 1 0 011-1h6a1 1 0 011 1v2a1 1 0 01-1 1H5a1 1 0 01-1-1v-2zM17 11a1 1 0 011-1h1a1 1 0 011 1v2a1 1 0 01-1 1h-1a1 1 0 01-1-1v-2zM4 17a1 1 0 011-1h14a1 1 0 011 1v2a1 1 0 01-1 1H5a1 1 0 01-1-1v-2z"/></svg>
                <span class="text-xs text-gray-400">Architecture Diagram</span>
              </div>
              <button class="text-xs text-[#58a6ff] hover:text-[#79b8ff]">Open full view</button>
            </div>
            <div class="p-4">
              <div class="bg-[#0d1117] rounded p-6 text-center">
                <pre class="text-xs text-gray-400 font-mono">
     [Internet]
        |
     [ALB]
     /   \
[API-1] [API-2] [API-3]
  \     |     /
    [PostgreSQL]
    [Redis Cache]</pre>
              </div>
            </div>
          </div>

          <!-- Approval card -->
          <div class="bg-[#161b22] border border-[#30363d] rounded-lg overflow-hidden">
            <div class="bg-[#0d1117] px-4 py-2 flex items-center gap-2 border-b border-[#30363d]">
              <svg class="w-4 h-4 text-yellow-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/></svg>
              <span class="text-xs text-gray-400">Approval Required</span>
            </div>
            <div class="p-4">
              <p class="text-sm text-gray-100 mb-3">The architecture plan is ready. This will provision:</p>
              <ul class="text-sm text-gray-300 list-disc list-inside mb-4 space-y-1">
                <li>Application Load Balancer with SSL termination</li>
                <li>Auto-scaling group (2-6 instances)</li>
                <li>Managed PostgreSQL cluster (db.r6g.large)</li>
                <li>ElastiCache Redis cluster (cache.r6g.large)</li>
                <li>VPC with public/private subnets</li>
              </ul>
              <div class="flex gap-2">
                <button class="bg-[#238636] hover:bg-[#2ea043] text-white px-4 py-2 rounded text-sm font-medium transition-colors">
                  Approve & Execute
                </button>
                <button class="bg-[#0d1117] border border-[#30363d] hover:border-[#58a6ff] text-gray-300 px-4 py-2 rounded text-sm transition-colors">
                  Modify
                </button>
                <button class="bg-[#0d1117] border border-[#30363d] hover:border-[#58a6ff] text-gray-300 px-4 py-2 rounded text-sm transition-colors">
                  Reject
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
          placeholder="Adjust parameters, ask questions, or approve..."
          class="flex-1 bg-[#0d1117] border border-[#30363d] rounded-lg px-4 py-3 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-[#58a6ff]"
        />
        <button class="bg-[#238636] hover:bg-[#2ea043] text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors">
          Send
        </button>
      </div>
    </div>
  </div>
</template>