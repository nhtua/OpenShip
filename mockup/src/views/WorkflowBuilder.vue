<template>
  <div class="h-full flex flex-col">
    <!-- Header -->
    <div class="border-b border-[#30363d] px-6 py-4 bg-[#161b22]">
      <div class="flex items-center justify-between">
        <div>
          <h1 class="text-lg font-semibold text-gray-100">Workflow Builder</h1>
          <p class="text-sm text-gray-400">Build custom workflows with the agent</p>
        </div>
        <div class="flex items-center gap-2">
          <button class="bg-[#0d1117] border border-[#30363d] hover:border-[#58a6ff] text-gray-300 px-3 py-1.5 rounded text-sm transition-colors">
            Export workflow file
          </button>
          <button class="bg-[#0d1117] border border-[#30363d] hover:border-[#58a6ff] text-gray-300 px-3 py-1.5 rounded text-sm transition-colors">
            Save
          </button>
          <button class="bg-[#238636] hover:bg-[#2ea043] text-white px-3 py-1.5 rounded text-sm font-medium transition-colors">
            Run in project
          </button>
        </div>
      </div>
    </div>

    <!-- Chat Stream -->
    <div class="flex-1 overflow-y-auto p-6 space-y-4">
      <!-- Agent greeting -->
      <div class="flex gap-3">
        <div class="w-8 h-8 bg-[#58a6ff] rounded-lg flex items-center justify-center flex-shrink-0">
          <span class="text-white text-xs font-bold">AI</span>
        </div>
        <div class="max-w-2xl">
          <div class="bg-[#161b22] border border-[#30363d] rounded-lg px-4 py-3">
            <p class="text-sm text-gray-100">I'll help you build a custom workflow. Describe what you want to automate and I'll help you discover the right tools, compose the steps, and validate the workflow.</p>
          </div>
        </div>
      </div>

      <!-- User describes workflow -->
      <div class="flex justify-end">
        <div class="max-w-2xl bg-[#238636]/20 border border-[#238636]/30 rounded-lg px-4 py-3">
          <p class="text-sm text-gray-100">I want to create a workflow that takes a GitHub issue, creates a Jira ticket, and posts a link to our Slack channel.</p>
        </div>
      </div>

      <!-- Agent asks clarifying questions -->
      <div class="flex gap-3">
        <div class="w-8 h-8 bg-[#58a6ff] rounded-lg flex items-center justify-center flex-shrink-0">
          <span class="text-white text-xs font-bold">AI</span>
        </div>
        <div class="max-w-2xl space-y-3">
          <div class="bg-[#161b22] border border-[#30363d] rounded-lg px-4 py-3">
            <p class="text-sm text-gray-100">Let me ask a few clarifying questions to build this properly.</p>
          </div>

          <!-- Tool discovery suggestion -->
          <div class="bg-[#161b22] border border-[#30363d] rounded-lg overflow-hidden">
            <div class="bg-[#0d1117] px-4 py-2 flex items-center gap-2 border-b border-[#30363d]">
              <svg class="w-4 h-4 text-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg>
              <span class="text-xs text-gray-400">Tool Discovery</span>
            </div>
            <div class="p-4">
              <p class="text-sm text-gray-100 mb-3">I found these relevant connectors and tools:</p>
              <div class="space-y-2">
                <div class="flex items-center gap-2 bg-[#0d1117] rounded px-3 py-2">
                  <span class="w-2 h-2 bg-green-400 rounded-full"></span>
                  <span class="text-xs text-gray-300">github (connected)</span>
                  <span class="text-xs text-gray-500 ml-auto">Issues API</span>
                </div>
                <div class="flex items-center gap-2 bg-[#0d1117] rounded px-3 py-2">
                  <span class="w-2 h-2 bg-yellow-400 rounded-full"></span>
                  <span class="text-xs text-gray-300">jira (not connected)</span>
                  <span class="text-xs text-gray-500 ml-auto">Issue creation</span>
                </div>
                <div class="flex items-center gap-2 bg-[#0d1117] rounded px-3 py-2">
                  <span class="w-2 h-2 bg-green-400 rounded-full"></span>
                  <span class="text-xs text-gray-300">slack (connected)</span>
                  <span class="text-xs text-gray-500 ml-auto">Message posting</span>
                </div>
              </div>
            </div>
          </div>

          <div class="bg-[#161b22] border border-[#30363d] rounded-lg px-4 py-3">
            <p class="text-sm text-gray-100">Do you want the workflow to automatically sync new GitHub issues to Jira, or should it be manually triggered? And which Jira project should tickets be created in?</p>
          </div>
        </div>
      </div>

      <!-- User answers -->
      <div class="flex justify-end">
        <div class="max-w-2xl bg-[#238636]/20 border border-[#238636]/30 rounded-lg px-4 py-3">
          <p class="text-sm text-gray-100">Manual trigger. Create tickets in the "PLATFORM" project with default priority "medium".</p>
        </div>
      </div>

      <!-- Agent proposes workflow -->
      <div class="flex gap-3">
        <div class="w-8 h-8 bg-[#58a6ff] rounded-lg flex items-center justify-center flex-shrink-0">
          <span class="text-white text-xs font-bold">AI</span>
        </div>
        <div class="max-w-2xl space-y-3">
          <div class="bg-[#161b22] border border-[#30363d] rounded-lg px-4 py-3">
            <p class="text-sm text-gray-100">Here's the workflow I've composed:</p>
          </div>

          <!-- Workflow definition card -->
          <div class="bg-[#161b22] border border-[#30363d] rounded-lg overflow-hidden">
            <div class="bg-[#0d1117] px-4 py-2 flex items-center gap-2 border-b border-[#30363d]">
              <svg class="w-4 h-4 text-purple-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg>
              <span class="text-xs text-gray-400">Workflow Definition</span>
            </div>
            <div class="p-4 font-mono text-xs text-gray-300">
<pre>name: github-issue-to-jira
version: 1.0.0
trigger: manual
inputs:
  issue_number: string
  project: string (default: "PLATFORM")

steps:
  1. Fetch issue from GitHub
     tool: github.issues.get
     inputs: { number: &#123;&#123; issue_number &#125;&#125; }
   
  2. Create Jira ticket
     tool: jira.create_issue
     inputs:
       project: &#123;&#123; project &#125;&#125;
       summary: "GH-&#123;&#123; issue_number &#125;&#125;: &#123;&#123; issue.title &#125;&#125;"
       description: &#123;&#123; issue.body &#125;&#125;
   
  3. Post to Slack
     tool: slack.postMessage
     inputs:
       channel: "#platform-ops"
       text: "Created Jira &#123;&#123; jira_ticket.key &#125;&#125; for GitHub issue #&#123;&#123; issue_number &#125;&#125;"</pre>
            </div>
          </div>

          <div class="bg-[#161b22] border border-[#30363d] rounded-lg px-4 py-3">
            <p class="text-sm text-gray-100">This workflow is ready. You can save it to your project, export it as a workflow file, or run it immediately in a project context.</p>
          </div>
        </div>
      </div>
    </div>

    <!-- Chat input -->
    <div class="border-t border-[#30363d] p-4 bg-[#161b22]">
      <div class="flex gap-3">
        <input
          type="text"
          placeholder="Refine the workflow, add steps, adjust parameters..."
          class="flex-1 bg-[#0d1117] border border-[#30363d] rounded-lg px-4 py-3 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-[#58a6ff]"
        />
        <button class="bg-[#238636] hover:bg-[#2ea043] text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors">
          Send
        </button>
      </div>
    </div>
  </div>
</template>