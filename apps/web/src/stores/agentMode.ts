import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { preferencesApi } from '@/services/api'
import type { AgentMode, AgentPreferences } from '@/types'

// The account's execution mode, owned by the account and read back from it.
// Nothing here keeps a browser-side copy of the choice: the committed
// selection is whatever the account last returned, and a mode the browser
// only asked for stays visibly unsaved until the server confirms it. A save
// that the server refused (including a revision this browser never saw) leaves
// the committed mode alone and blocks a task send that would otherwise have
// to guess which mode to run.
export const useAgentModeStore = defineStore('agentMode', () => {
  const snapshot = ref<AgentPreferences | null>(null)
  const pendingMode = ref<AgentMode | null>(null)
  const loading = ref(false)
  const saving = ref(false)
  const conflict = ref(false)
  const loadFailed = ref(false)
  const error = ref<string | null>(null)
  let requestGeneration = 0

  const mode = computed<AgentMode | null>(() => snapshot.value?.agent_mode ?? null)
  const revision = computed(() => snapshot.value?.revision ?? 0)

  // A send may pin a mode only while the account's selection is settled: not
  // while it is being read, being saved, known to be stale, or failed to load.
  const submissionBlocked = computed(() => Boolean(
    loading.value || saving.value || conflict.value || loadFailed.value ||
    (pendingMode.value !== null && pendingMode.value !== snapshot.value?.agent_mode),
  ))

  function label(value: AgentMode): string {
    return value === 'auto' ? 'Auto' : 'Copilot'
  }

  async function load(): Promise<void> {
    const generation = ++requestGeneration
    loading.value = true
    loadFailed.value = false
    try {
      const response = await preferencesApi.get()
      if (generation !== requestGeneration) return
      snapshot.value = response.data as unknown as AgentPreferences
      pendingMode.value = null
      conflict.value = false
      error.value = null
    } catch (failure: unknown) {
      if (generation !== requestGeneration) return
      loadFailed.value = true
      error.value = 'The agent mode could not be loaded. Retry before a task send.'
    } finally {
      if (generation === requestGeneration) loading.value = false
    }
  }

  async function save(mode: AgentMode): Promise<void> {
    const current = snapshot.value
    if (!current || loadFailed.value) {
      // Without the account's current revision there is nothing to compare
      // against, so the wish stays pending and the send stays blocked.
      if (current) pendingMode.value = mode
      error.value = 'The agent mode could not be saved. Reload the preference and try again.'
      return
    }
    if (current.agent_mode === mode) {
      pendingMode.value = null
      return
    }

    const generation = ++requestGeneration
    pendingMode.value = mode
    saving.value = true
    error.value = null
    try {
      const response = await preferencesApi.update({
        agent_mode: mode,
        expected_revision: current.revision,
      })
      if (generation !== requestGeneration) {
        await load()
        return
      }
      snapshot.value = response.data as unknown as AgentPreferences
      pendingMode.value = null
      conflict.value = false
      loadFailed.value = false
      error.value = null
    } catch (failure: unknown) {
      if (generation !== requestGeneration) return
      if (responseStatus(failure) === 409) {
        // The account moved first: adopt what it says rather than re-asking,
        // and say plainly that this browser's choice was not what applied.
        conflict.value = true
        await load()
        conflict.value = false
        error.value = 'Another tab saved this preference first. The current account mode has been loaded.'
      } else {
        error.value = `The agent mode could not be saved. The account still uses ${label(current.agent_mode)}.`
      }
    } finally {
      if (generation === requestGeneration) saving.value = false
    }
  }

  function submissionSelection(): { agent_mode: AgentMode; agent_mode_revision: number } | null {
    if (submissionBlocked.value) return null
    const current = snapshot.value
    if (!current) return null
    return { agent_mode: current.agent_mode, agent_mode_revision: current.revision }
  }

  // A logout or an account switch discards in-flight reads as well as the
  // committed selection: a late response for the previous account must not
  // become this account's mode.
  function reset(): void {
    requestGeneration += 1
    snapshot.value = null
    pendingMode.value = null
    loading.value = false
    saving.value = false
    conflict.value = false
    loadFailed.value = false
    error.value = null
  }

  return {
    mode,
    revision,
    pendingMode,
    loading,
    saving,
    conflict,
    loadFailed,
    error,
    submissionBlocked,
    load,
    save,
    submissionSelection,
    reset,
  }
})

function responseStatus(failure: unknown): number | undefined {
  return (failure as { response?: { status?: number } })?.response?.status
}
