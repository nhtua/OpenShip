<template>
  <div class="stage-item" :class="{ active: isActive, completed: isCompleted }">
    <div class="stage-indicator">
      <div v-if="isCompleted" class="indicator-dot completed">✓</div>
      <div v-else-if="isProcessing" class="indicator-dot processing">
        <svg class="spinner" viewBox="0 0 20 20">
          <circle cx="10" cy="10" r="8" stroke="currentColor" stroke-width="2" fill="none"
                  stroke-dasharray="40" stroke-dashoffset="10">
            <animateTransform attributeName="transform" type="rotate"
                              from="0 10 10" to="360 10 10" dur="1s" repeatCount="indefinite"/>
          </circle>
        </svg>
      </div>
      <div v-else-if="isActive" class="indicator-dot active"></div>
      <div v-else class="indicator-dot pending"></div>
    </div>
    <div class="stage-label">
      <span>{{ label }}</span>
      <span v-if="statusMessage" class="status-message">{{ statusMessage }}</span>
    </div>
    <div v-if="showConnector" class="stage-connector"></div>
  </div>
</template>

<script setup>
defineProps({
  label: String,
  isActive: Boolean,
  isCompleted: Boolean,
  isProcessing: Boolean,
  statusMessage: String,
  showConnector: Boolean
})
</script>

<style scoped>
.stage-item {
  display: flex;
  align-items: center;
  padding: 8px 0;
  position: relative;
}

.indicator-dot {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  margin-right: 12px;
  flex-shrink: 0;
}

.indicator-dot.completed {
  background: #4af62c;
  color: #1a1a1a;
}

.indicator-dot.processing {
  background: rgba(74, 246, 44, 0.2);
  color: #4af62c;
}

.indicator-dot.active {
  background: #4af62c;
  color: #1a1a1a;
}

.indicator-dot.pending {
  background: #333;
  color: #666;
}

.stage-label {
  display: flex;
  flex-direction: column;
}

.stage-label span:first-child {
  color: #e0e0e0;
  font-size: 14px;
}

.status-message {
  color: #4af62c;
  font-size: 11px;
  font-style: italic;
}

.stage-connector {
  position: absolute;
  left: 9px;
  top: 28px;
  width: 2px;
  height: 100%;
  background: #333;
}

.stage-item:last-child .stage-connector {
  display: none;
}

.spinner {
  width: 16px;
  height: 16px;
}
</style>