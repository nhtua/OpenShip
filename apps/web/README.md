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

## Navigation

`/` opens the home/new-chat view. Each saved conversation has a URL at
`/conversation/<conversation_id>`. Selecting or creating a conversation adds
its URL to browser history; Back and Forward restore the selected conversation.
Reloading or opening a bookmarked URL loads its latest saved messages, tool
activity, files, and run status. Conversation access follows the API's ownership
checks. Missing or inaccessible conversations display an error at their URL.

Conversation links opened while signed out return to the requested conversation
after sign-in or registration. Return URLs are restricted to registered,
authenticated application routes.

Production hosting must serve `index.html` for frontend routes while preserving
`/api/` requests. The included `nginx.conf` provides this fallback. Future sidebar
screens will have their own routes, such as `/workflows`, `/tools`, `/skills`,
`/artifacts`, and `/connectors`, as those screens are implemented.

## License

MIT
