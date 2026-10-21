<script setup lang="ts">
import { computed } from 'vue'
import { renderMarkdown } from '@/lib/markdown'

const props = defineProps<{ content: string }>()
const rendered = computed(() => renderMarkdown(props.content))
</script>

<template>
  <div
    class="markdown-content min-w-0 max-w-full text-sm leading-6 [overflow-wrap:anywhere]"
    data-testid="markdown-content"
    v-html="rendered"
  />
</template>

<style scoped>
.markdown-content :deep(p) {
  margin-block: 0.55rem;
}

.markdown-content :deep(p:first-child),
.markdown-content :deep(ul:first-child),
.markdown-content :deep(ol:first-child),
.markdown-content :deep(blockquote:first-child),
.markdown-content :deep(pre:first-child),
.markdown-content :deep(h1:first-child),
.markdown-content :deep(h2:first-child),
.markdown-content :deep(h3:first-child) {
  margin-top: 0;
}

.markdown-content :deep(p:last-child),
.markdown-content :deep(ul:last-child),
.markdown-content :deep(ol:last-child),
.markdown-content :deep(blockquote:last-child),
.markdown-content :deep(pre:last-child),
.markdown-content :deep(h1:last-child),
.markdown-content :deep(h2:last-child),
.markdown-content :deep(h3:last-child) {
  margin-bottom: 0;
}

.markdown-content :deep(h1),
.markdown-content :deep(h2),
.markdown-content :deep(h3) {
  margin-block: 0.9rem 0.4rem;
  font-weight: 650;
  line-height: 1.3;
}

.markdown-content :deep(h1) { font-size: 1.35em; }
.markdown-content :deep(h2) { font-size: 1.2em; }
.markdown-content :deep(h3) { font-size: 1.08em; }

.markdown-content :deep(ul),
.markdown-content :deep(ol) {
  margin-block: 0.55rem;
  padding-left: 1.5rem;
}

.markdown-content :deep(ul) { list-style: disc; }
.markdown-content :deep(ol) { list-style: decimal; }
.markdown-content :deep(li + li) { margin-top: 0.2rem; }

.markdown-content :deep(a) {
  color: var(--primary);
  text-decoration: underline;
  text-underline-offset: 0.15em;
}

.markdown-content :deep(blockquote) {
  margin-block: 0.65rem;
  border-left: 3px solid var(--border);
  padding-left: 0.75rem;
  color: var(--muted-foreground);
}

.markdown-content :deep(pre) {
  max-width: 100%;
  margin-block: 0.7rem;
  overflow-x: auto;
  border: 1px solid var(--border);
  border-radius: 0.5rem;
  background: var(--muted);
  padding: 0.75rem;
  font-size: 0.85em;
  line-height: 1.5;
}

.markdown-content :deep(code) {
  border-radius: 0.25rem;
  background: var(--muted);
  padding: 0.08em 0.3em;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 0.9em;
}

.markdown-content :deep(pre code) {
  background: transparent;
  padding: 0;
  font-size: inherit;
  white-space: pre;
}

.markdown-content :deep(hr) {
  margin-block: 0.8rem;
  border: 0;
  border-top: 1px solid var(--border);
}

.markdown-content :deep(table) {
  display: block;
  max-width: 100%;
  overflow-x: auto;
  border-collapse: collapse;
}

.markdown-content :deep(th),
.markdown-content :deep(td) {
  border: 1px solid var(--border);
  padding: 0.3rem 0.5rem;
  text-align: left;
}
</style>
