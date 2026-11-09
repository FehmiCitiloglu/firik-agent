import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: { include: ['apps/desktop/tests/**/*.test.ts'], environment: 'node', testTimeout: 20000 },
});
