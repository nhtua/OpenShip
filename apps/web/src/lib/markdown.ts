import MarkdownIt from 'markdown-it'

const renderer = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
  typographer: false,
})

export function renderMarkdown(content: string): string {
  return renderer.render(content)
}
