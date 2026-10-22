<script setup lang="ts">
import { RouterLink } from 'vue-router'
import { useConnectorsStore } from '@/stores/connectors'

const connectors = useConnectorsStore()

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
        Open Connectors
      </RouterLink>
    </section>
  </section>
</template>
