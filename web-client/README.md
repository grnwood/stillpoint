# StillPoint Web Client

Mobile-first Progressive Web App (PWA) for StillPoint with offline support.

## Features

- **JWT Authentication** - Secure token-based auth with auto-refresh
- **Offline Support** - IndexedDB caching with background sync
- **Conflict Resolution** - Manual merge UI for conflicting edits
- **Mobile-First UI** - Touch-optimized interface
- **PWA** - Installable on mobile devices

## Development

```bash
# Install dependencies
npm install

# Start dev server (proxies to localhost:8000)
npm run dev

# Build for production
npm run build

# Preview production build
npm run preview
```

## Environment Variables

Create `.env.local`:

```
VITE_API_BASE_URL=http://localhost:8000
```

## Architecture

- **React + TypeScript** - UI framework
- **Dexie.js** - IndexedDB wrapper for offline storage
- **Vite PWA** - Service worker and PWA manifest
- **Sync Manager** - Background sync with conflict detection

### Offline Strategy

1. **Pull Sync**: Polls `/sync/changes` every 30 seconds when online
2. **Push Sync**: Queues edits in IndexedDB outbox when offline
3. **Conflict Handling**: If-Match header with 409 conflict detection

### Storage

- **pages** - Cached page content and metadata
- **tree** - Cached folder tree structure
- **tasks** - Cached tasks for offline access
- **outbox** - Queued edits waiting to sync

## Backend Requirements

The backend server must support:

- `/auth/*` endpoints (setup, login, refresh, logout)
- `/sync/changes` - Incremental change feed
- `/recent` - Recent pages
- `/tags` - Tag aggregation
- `If-Match` header on `/api/file/write` for conflict detection

## TODO

- [ ] Rich markdown editor component
- [ ] Task list view with filters
- [ ] Search page
- [ ] Browse/tree navigation
- [ ] Conflict resolution UI
- [ ] Pull-to-refresh
- [ ] Bottom navigation tabs


Currently, two official plugins are available:

- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react) uses [Babel](https://babeljs.io/) (or [oxc](https://oxc.rs) when used in [rolldown-vite](https://vite.dev/guide/rolldown)) for Fast Refresh
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react-swc) uses [SWC](https://swc.rs/) for Fast Refresh

## React Compiler

The React Compiler is not enabled on this template because of its impact on dev & build performances. To add it, see [this documentation](https://react.dev/learn/react-compiler/installation).

## Expanding the ESLint configuration

If you are developing a production application, we recommend updating the configuration to enable type-aware lint rules:

```js
export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      // Other configs...

      // Remove tseslint.configs.recommended and replace with this
      tseslint.configs.recommendedTypeChecked,
      // Alternatively, use this for stricter rules
      tseslint.configs.strictTypeChecked,
      // Optionally, add this for stylistic rules
      tseslint.configs.stylisticTypeChecked,

      // Other configs...
    ],
    languageOptions: {
      parserOptions: {
        project: ['./tsconfig.node.json', './tsconfig.app.json'],
        tsconfigRootDir: import.meta.dirname,
      },
      // other options...
    },
  },
])
```

You can also install [eslint-plugin-react-x](https://github.com/Rel1cx/eslint-react/tree/main/packages/plugins/eslint-plugin-react-x) and [eslint-plugin-react-dom](https://github.com/Rel1cx/eslint-react/tree/main/packages/plugins/eslint-plugin-react-dom) for React-specific lint rules:

```js
// eslint.config.js
import reactX from 'eslint-plugin-react-x'
import reactDom from 'eslint-plugin-react-dom'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      // Other configs...
      // Enable lint rules for React
      reactX.configs['recommended-typescript'],
      // Enable lint rules for React DOM
      reactDom.configs.recommended,
    ],
    languageOptions: {
      parserOptions: {
        project: ['./tsconfig.node.json', './tsconfig.app.json'],
        tsconfigRootDir: import.meta.dirname,
      },
      // other options...
    },
  },
])
```
