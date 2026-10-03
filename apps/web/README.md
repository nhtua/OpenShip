# OpenShip Web

The web frontend for OpenShip — an agentic DevOps co-pilot for developers and platform engineers.

## Tech Stack

- **Vue 3.5+** with `<script setup lang="ts">`
- **Vite 6** for blazing-fast dev server and builds
- **TypeScript 5.6** for type safety
- **Pinia 2.2** for state management
- **Vue Router 4.4** for client-side routing
- **Tailwind CSS 4** for utility-first styling
- **shadcn-vue** (via Radix Vue) for accessible UI components
- **Axios 1.7** for HTTP requests
- **@vueuse/core 11** for composable utilities

## Getting Started

```bash
# Install dependencies
pnpm install

# Start dev server
pnpm dev

# Build for production
pnpm build

# Preview production build
pnpm preview
```

## Project Structure

```
apps/web/
├── index.html              # Entry HTML
├── vite.config.js          # Vite configuration
├── tsconfig.json           # TypeScript configuration
├── package.json            # Dependencies and scripts
└── src/
    ├── main.ts             # Application entry point
    ├── App.vue             # Root component
    ├── router.ts           # Vue Router configuration
    └── views/
        ├── LoginView.vue   # Login page
        ├── RegisterView.vue # Registration page
        └── ChatView.vue    # Chat interface
```

## Development

The dev server proxies API requests to the backend at `http://localhost:8000`. Start the backend server separately before testing.

## License

MIT
