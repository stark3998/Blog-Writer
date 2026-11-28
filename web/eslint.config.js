import js from '@eslint/js';
import { defineConfig, globalIgnores } from 'eslint/config';
import reactHooks from 'eslint-plugin-react-hooks';
import globals from 'globals';
import tseslint from 'typescript-eslint';

export default defineConfig([
  globalIgnores(['dist', 'coverage', 'node_modules']),

  // Application and test code: type-aware, strict.
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.strictTypeChecked,
      tseslint.configs.stylisticTypeChecked,
      reactHooks.configs.flat['recommended-latest'],
    ],
    languageOptions: {
      ecmaVersion: 2023,
      globals: globals.browser,
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    rules: {
      '@typescript-eslint/consistent-type-imports': ['error', { fixStyle: 'inline-type-imports' }],
      '@typescript-eslint/no-import-type-side-effects': 'error',
      '@typescript-eslint/restrict-template-expressions': ['error', { allowNumber: true }],
      // Exhaustive switches over string unions are checked by the compiler.
      '@typescript-eslint/switch-exhaustiveness-check': 'error',
      eqeqeq: ['error', 'always'],
      'no-console': ['error', { allow: ['warn', 'error'] }],
    },
  },

  // Build scripts run in Node.
  {
    files: ['scripts/**/*.mjs', 'eslint.config.js'],
    extends: [js.configs.recommended],
    languageOptions: { ecmaVersion: 2023, sourceType: 'module', globals: globals.node },
  },

  // Plain scripts served from public/: the theme bootstrap runs in the page, sw.js in the worker.
  {
    files: ['public/theme-init.js'],
    extends: [js.configs.recommended],
    languageOptions: { ecmaVersion: 2019, sourceType: 'script', globals: globals.browser },
  },
  {
    files: ['public/sw.js'],
    extends: [js.configs.recommended],
    languageOptions: { ecmaVersion: 2020, sourceType: 'script', globals: globals.serviceworker },
  },
]);
