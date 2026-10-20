<template>
  <div class="h-full flex flex-col">
    <!-- Header -->
    <div class="border-b border-[#30363d] px-6 py-4 bg-[#161b22]">
      <div class="flex items-center justify-between">
        <div>
          <h1 class="text-lg font-semibold text-gray-100">CI/CD Setup</h1>
          <p class="text-sm text-gray-400">web-platform • Session #1044</p>
        </div>
        <div class="flex items-center gap-3">
          <span class="flex items-center gap-2 text-sm text-green-400">
            <span class="w-2 h-2 bg-green-400 rounded-full animate-pulse"></span>
            Running
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
          <p class="text-sm text-gray-100">Set up CI/CD for my web platform. I want automated testing on every PR, and auto-deploy to staging on merge to main.</p>
        </div>
      </div>

      <!-- Agent: repo connection -->
      <div class="flex gap-3">
        <div class="w-8 h-8 bg-[#58a6ff] rounded-lg flex items-center justify-center flex-shrink-0">
          <span class="text-white text-xs font-bold">AI</span>
        </div>
        <div class="max-w-2xl">
          <div class="bg-[#161b22] border border-[#30363d] rounded-lg px-4 py-3 mb-3">
            <p class="text-sm text-gray-100">I'll set up GitHub Actions for your platform. Let me connect to your repository first.</p>
          </div>
          <div class="bg-[#161b22] border border-[#30363d] rounded-lg p-4">
            <div class="flex items-center gap-2 mb-3">
              <span class="w-2 h-2 bg-green-400 rounded-full"></span>
              <span class="text-sm text-gray-100 font-medium">Repository connected</span>
            </div>
            <p class="text-xs text-gray-400">web-platform-team/web-platform • main branch</p>
          </div>
        </div>
      </div>

      <!-- Agent: workflow generation -->
      <div class="flex gap-3">
        <div class="w-8 h-8 bg-[#58a6ff] rounded-lg flex items-center justify-center flex-shrink-0">
          <span class="text-white text-xs font-bold">AI</span>
        </div>
        <div class="max-w-2xl space-y-3">
          <div class="bg-[#161b22] border border-[#30363d] rounded-lg px-4 py-3">
            <p class="text-sm text-gray-100">I've generated the CI/CD workflow. Here's the configuration:</p>
          </div>
          <div class="bg-[#161b22] border border-[#30363d] rounded-lg overflow-hidden">
            <div class="bg-[#0d1117] px-4 py-2 flex items-center justify-between border-b border-[#30363d]">
              <span class="text-xs text-gray-400">.github/workflows/ci-cd.yml</span>
              <button class="text-xs text-[#58a6ff] hover:text-[#79b8ff]">View full file</button>
            </div>
            <div class="p-4 font-mono text-xs text-gray-300">
<pre>name: CI/CD

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run tests
        run: make test

  deploy-staging:
    needs: test
    if: github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Deploy to staging
        run: make deploy-staging</pre>
            </div>
          </div>
        </div>
      </div>

      <!-- Approval -->
      <div class="flex gap-3">
        <div class="w-8 h-8 bg-[#58a6ff] rounded-lg flex items-center justify-center flex-shrink-0">
          <span class="text-white text-xs font-bold">AI</span>
        </div>
        <div class="max-w-2xl">
          <div class="bg-[#161b22] border border-[#30363d] rounded-lg overflow-hidden">
            <div class="bg-[#0d1117] px-4 py-2 flex items-center gap-2 border-b border-[#30363d]">
              <svg class="w-4 h-4 text-yellow-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/></svg>
              <span class="text-xs text-gray-400">Approval Required</span>
            </div>
            <div class="p-4">
              <p class="text-sm text-gray-100 mb-3">Push the workflow to your repository?</p>
              <div class="flex gap-2">
                <button class="bg-[#238636] hover:bg-[#2ea043] text-white px-4 py-2 rounded text-sm font-medium transition-colors">
                  Push to GitHub
                </button>
                <button class="bg-[#0d1117] border border-[#30363d] hover:border-[#58a6ff] text-gray-300 px-4 py-2 rounded text-sm transition-colors">
                  Modify
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Run status -->
      <div class="flex gap-3">
        <div class="w-8 h-8 bg-[#58a6ff] rounded-lg flex items-center justify-center flex-shrink-0">
          <span class="text-white text-xs font-bold">AI</span>
        </div>
        <div class="max-w-2xl space-y-3">
          <div class="bg-[#161b22] border border-[#30363d] rounded-lg px-4 py-3">
            <p class="text-sm text-gray-100">The workflow has been pushed. I've triggered an initial run:</p>
          </div>
          <div class="bg-[#161b22] border border-[#30363d] rounded-lg overflow-hidden">
            <div class="bg-[#0d1117] px-4 py-2 flex items-center justify-between border-b border-[#30363d]">
              <span class="text-xs text-gray-400">Workflow Run #1</span>
              <span class="flex items-center gap-1 text-xs text-blue-400">
                <span class="w-2 h-2 bg-blue-400 rounded-full animate-pulse"></span>
                In progress
              </span>
            </div>
            <div class="p-4 space-y-2">
              <div class="flex items-center gap-2">
                <svg class="w-4 h-4 text-green-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>
                <span class="text-xs text-gray-300">test</span>
                <span class="text-xs text-gray-500 ml-auto">42s</span>
              </div>
              <div class="flex items-center gap-2">
                <svg class="w-4 h-4 text-blue-400 animate-spin" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
                <span class="text-xs text-gray-300">deploy-staging</span>
                <span class="text-xs text-gray-500 ml-auto">12s</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Recent runs -->
      <div class="flex gap-3">
        <div class="w-8 h-8 bg-[#58a6ff] rounded-lg flex items-center justify-center flex-shrink-0">
          <span class="text-white text-xs font-bold">AI</span>
        </div>
        <div class="max-w-2xl">
          <div class="bg-[#161b22] border border-[#30363d] rounded-lg overflow-hidden">
            <div class="bg-[#0d1117] px-4 py-2 border-b border-[#30363d]">
              <span class="text-xs text-gray-400">Recent Runs</span>
            </div>
            <div class="divide-y divide-[#30363d]">
              <div class="flex items-center justify-between px-4 py-3">
                <div>
                  <p class="text-sm text-gray-100">CI/CD #1</p>
                  <p class="text-xs text-gray-500">Triggered by push to main • 2 minutes ago</p>
                </div>
                <span class="text-xs text-blue-400 bg-blue-500/10 px-2 py-1 rounded">in progress</span>
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
          placeholder="Ask about the run, manage workflows, or view logs..."
          class="flex-1 bg-[#0d1117] border border-[#30363d] rounded-lg px-4 py-3 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-[#58a6ff]"
        />
        <button class="bg-[#238636] hover:bg-[#2ea043] text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors">
          Send
        </button>
      </div>
    </div>
  </div>
</template>