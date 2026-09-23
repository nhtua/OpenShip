<template>
  <div class="workflow-timeline">
    <StageItem
      v-for="(stage, index) in stages"
      :key="stage.id"
      :label="stage.label"
      :is-active="activeStage === stage.id"
      :is-completed="completedStages.includes(stage.id)"
      :status-message="activeStage === stage.id ? stage.statusMessage : null"
      :show-connector="index < stages.length - 1"
    />
  </div>
</template>

<script setup>
import StageItem from './StageItem.vue'

const props = defineProps({
  stages: Array,
  activeStage: String,
  completedStages: Array
})
</script>

<style scoped>
.workflow-timeline {
  display: flex;
  flex-direction: column;
}
</style>