<script setup lang="ts">
import { onBeforeUnmount, onMounted } from 'vue'
import { RouterLink } from 'vue-router'
import { useConnectorsStore } from '@/stores/connectors'
import { useProjectsStore } from '@/stores/projects'
import { useToolPolicyStore } from '@/stores/toolPolicy'
import type { ToolPolicyEntry } from '@/types'

const connectors = useConnectorsStore()
const projects = useProjectsStore()
const toolPolicy = useToolPolicyStore()

const toolLabels: Record<string, string> = {
  brave_web_search: 'Brave Search',
  tavily_web_search: 'Tavily Search',
  aws_check_connection: 'AWS connection check',
  aws_s3_bucket_storage: 'S3 bucket storage',
  aws_s3_bucket_metadata: 'S3 bucket metadata',
  workspace_list_files: 'List workspace files',
  workspace_read_file: 'Read workspace file',
  workspace_search_files: 'Search workspace files',
}

function toolLabel(id: string): string {
  return toolLabels[id] ?? id.replaceAll('_', ' ')
}

async function refreshSettings(): Promise<void> {
  await Promise.all([
    toolPolicy.load(),
    connectors.loadProjectContext(projects.currentProjectId),
  ])
}

function onWindowFocus(): void {
  void refreshSettings()
}

onMounted(() => {
  void refreshSettings()
  window.addEventListener('focus', onWindowFocus)
})

onBeforeUnmount(() => {
  window.removeEventListener('focus', onWindowFocus)
})

async function toggleTool(tool: ToolPolicyEntry, event: Event): Promise<void> {
  const enabled = (event.target as HTMLInputElement).checked
  const saved = await toolPolicy.setEnabled(tool.id, enabled)
  if (saved) await connectors.loadProjectContext(projects.currentProjectId)
}

async function adoptLegacyAllowlist(): Promise<void> {
  const adopted = await toolPolicy.cutoverLegacyAllowlist()
  if (adopted) await connectors.loadProjectContext(projects.currentProjectId)
}

const unavailableMessage = () => {
  switch (connectors.setupUnavailableReason) {
    case 'installation_id_missing':
      return 'The installation identity is not configured.'
    case 'encryption_key_unavailable':
      return 'The shared credential encryption key is unavailable.'
    case 'idempotency_key_unavailable':
      return 'The shared request recovery key is unavailable.'
    default:
      return 'Setup readiness could not be confirmed. Ask the installation operator to check the API and worker configuration.'
  }
}

</script>

<template>
  <section class="grid gap-6" aria-labelledby="global-settings-heading">
    <header class="grid gap-1">
      <h1 id="global-settings-heading" class="text-2xl font-semibold">Global Settings</h1>
      <p class="text-sm text-muted-foreground">Installation-wide connector setup is managed by its configured operator.</p>
    </header>

    <section class="grid gap-3 rounded-xl border border-border bg-card p-4 md:p-6" aria-labelledby="installation-heading">
      <div>
        <h2 id="installation-heading" class="text-lg font-semibold">Installation</h2>
        <p class="text-sm text-muted-foreground">
          {{ connectors.canManageGlobal ? 'You can manage global connectors for this installation.' : 'Global connectors are managed by the installation operator.' }}
        </p>
      </div>
      <p v-if="connectors.globalLoading" role="status" class="text-sm text-muted-foreground">Checking installation readiness…</p>
      <p v-else-if="connectors.credentialStoreReady" role="status" class="text-sm text-muted-foreground">
        Shared connector credential storage is ready.
      </p>
      <p v-else role="status" class="text-sm text-muted-foreground">
        {{ unavailableMessage() }}
      </p>
      <RouterLink to="/connectors" class="w-fit text-sm font-medium text-primary underline-offset-4 hover:underline">
        Open Connector catalog
      </RouterLink>
    </section>

    <section class="grid gap-4 rounded-xl border border-border bg-card p-4 md:p-6" aria-labelledby="tool-availability-heading">
      <div>
        <h2 id="tool-availability-heading" class="text-lg font-semibold">Built-in tool availability</h2>
        <p class="text-sm text-muted-foreground">
          Choose which installed OpenShip tools can be used across projects. Connector and target readiness remains project-specific.
        </p>
      </div>

      <p v-if="toolPolicy.loading && !toolPolicy.snapshot" role="status" class="text-sm text-muted-foreground">
        Loading built-in tool availability…
      </p>
      <p v-else-if="toolPolicy.forbidden" role="status" class="text-sm text-muted-foreground">
        Only the installation operator can manage built-in tool availability.
      </p>
      <p v-else-if="toolPolicy.error" role="alert" class="text-sm text-destructive">
        {{ toolPolicy.error }}
      </p>

      <template v-else-if="toolPolicy.snapshot">
        <ul class="grid gap-3" aria-label="Installed built-in tools">
          <li
            v-for="tool in toolPolicy.tools"
            :key="tool.id"
            class="grid gap-3 rounded-lg border border-border p-3 sm:grid-cols-[1fr_auto] sm:items-center"
          >
            <div class="grid gap-1">
              <span class="font-medium">{{ toolLabel(tool.id) }}</span>
              <span class="text-sm text-muted-foreground">{{ tool.description }}</span>
              <span v-if="tool.deployment_restricted" class="text-sm text-amber-600">
                This tool is blocked by TOOL_ENABLED_IDS. Enabling it here cannot bypass that deployment ceiling.
              </span>
              <span v-else-if="tool.unavailable_reason === 'disabled_by_operator'" class="text-sm text-muted-foreground">
                Disabled in Global Settings.
              </span>
            </div>
            <label class="flex items-center gap-2 text-sm">
              <input
                :data-testid="`tool-policy-toggle-${tool.id}`"
                type="checkbox"
                :checked="tool.enabled"
                :disabled="toolPolicy.saving"
                @change="toggleTool(tool, $event)"
              >
              Enabled
            </label>
          </li>
        </ul>

        <section v-if="toolPolicy.deploymentCeilingActive" class="grid gap-2 rounded-lg border border-border p-3" aria-labelledby="deployment-ceiling-heading">
          <h3 id="deployment-ceiling-heading" class="font-medium">Deployment ceiling</h3>
          <p class="text-sm text-muted-foreground">
            TOOL_ENABLED_IDS limits the tools this installation can use, even when a tool is enabled above.
          </p>
          <p class="text-sm">
            Currently permitted by both settings:
            <span v-if="toolPolicy.effectiveIds.length">{{ toolPolicy.effectiveIds.map(toolLabel).join(', ') }}</span>
            <span v-else>No tools</span>
          </p>
          <p v-if="toolPolicy.unknownDeploymentIds.length" class="text-sm text-muted-foreground">
            Unknown tool IDs in TOOL_ENABLED_IDS: {{ toolPolicy.unknownDeploymentIds.join(', ') }}
          </p>
          <button
            v-if="toolPolicy.cutoverAvailable"
            data-testid="tool-policy-cutover"
            type="button"
            class="w-fit rounded-md border border-border px-3 py-2 text-sm hover:bg-muted disabled:opacity-50"
            :disabled="toolPolicy.saving"
            @click="adoptLegacyAllowlist"
          >
            Adopt current deployment allowlist
          </button>
          <p v-if="toolPolicy.cutoverAvailable" class="text-sm text-muted-foreground">
            This one-time action saves the current effective tool set in Global Settings. Review it above first.
          </p>
          <p v-if="toolPolicy.legacyCutoverAt" role="status" class="text-sm text-muted-foreground">
            Adoption is recorded. Remove TOOL_ENABLED_IDS from the API and worker environments, then restart both once.
          </p>
        </section>
      </template>
    </section>
  </section>
</template>
