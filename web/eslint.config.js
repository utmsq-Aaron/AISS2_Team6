// ESLint (flat config) for the React SPA.
//
// Companion to `npm run typecheck`, not a replacement: tsc checks *types*,
// this checks *patterns* tsc is blind to — above all incomplete React hook
// dependency arrays, the usual cause of "the view doesn't update".
//
//   npm run lint        # report
//   npm run lint:fix    # apply the safe fixes
//
// Deliberately close to the recommended presets. Rules are switched off only
// where the preset disagrees with a decision this code base has already made,
// and each of those carries its reason.

import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "node_modules"] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
    },
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,

      // Vite's fast refresh only works if a module exports components alone.
      // A warning, not an error: a few modules export a helper next to their
      // component on purpose.
      "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],

      // `_`-prefixed arguments are the established way here to mark a
      // deliberately unused parameter (event handlers, catch clauses).
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_", caughtErrorsIgnorePattern: "^_" },
      ],

      // MCP tool results arrive as untyped JSON. Typing every response shape
      // would be busywork that adds no safety at the boundary itself — the
      // narrowing happens where the data is read.
      "@typescript-eslint/no-explicit-any": "off",

      // A performance rule from the React-Compiler ruleset, not a correctness
      // one: `useEffect(() => setX(prop), [prop])` costs one extra render. It
      // fires at 14 places where local state mirrors a prop or a query result,
      // and every fix is a per-site restructure of working UI. Kept visible as
      // a warning — a deliberate pass, not a mechanical one.
      "react-hooks/set-state-in-effect": "warn",
    },
  },
);
