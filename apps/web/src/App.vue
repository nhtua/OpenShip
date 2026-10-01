<script setup lang="ts">
import { RouterView } from 'vue-router'
</script>

<template>
  <div class="app-container">
    <header class="app-header">
      <div class="header-content">
        <h1 class="app-title">OpenShip</h1>
        <nav class="header-nav">
          <button class="nav-btn active">Home</button>
          <button class="nav-btn">Workflows</button>
          <button class="nav-btn">Tools</button>
        </nav>
      </div>
    </header>
    <main class="app-main">
      <RouterView />
    </main>
  </div>
</template>

<style>
:root {
  --primary: #2563eb;
  --primary-hover: #1d4ed8;
  --bg: #f9fafb;
  --surface: #ffffff;
  --border: #e5e7eb;
  --text: #111827;
  --text-muted: #6b7280;
  --success: #10b981;
  --warning: #f59e0b;
  --error: #ef4444;
}

* {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: var(--bg);
  color: var(--text);
  line-height: 1.5;
}

.app-container {
  display: flex;
  flex-direction: column;
  min-height: 100vh;
}

.app-header {
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  padding: 0.5rem 1rem;
}

.header-content {
  display: flex;
  justify-content: space-between;
  align-items: center;
  max-width: 1200px;
  margin: 0 auto;
}

.app-title {
  font-size: 1.25rem;
  font-weight: 600;
  color: var(--primary);
}

.header-nav {
  display: flex;
  gap: 0.5rem;
}

.nav-btn {
  padding: 0.375rem 0.75rem;
  border: none;
  background: transparent;
  border-radius: 0.25rem;
  font-size: 0.875rem;
  color: var(--text-muted);
  cursor: pointer;
}

.nav-btn:hover {
  background: var(--bg);
  color: var(--text);
}

.nav-btn.active {
  background: var(--bg);
  color: var(--primary);
  font-weight: 500;
}

.app-main {
  flex: 1;
  padding: 1rem;
  overflow: hidden;
}
</style>