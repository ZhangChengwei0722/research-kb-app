import eslint from "@eslint/js";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["web/dist/**", "web/release/**", "node_modules/**", "playwright-report/**", "test-results/**"] },
  eslint.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.ts", "**/*.tsx"],
    languageOptions: {
      globals: {
        document: "readonly",
        fetch: "readonly",
        HTMLInputElement: "readonly",
        process: "readonly",
        RequestInit: "readonly",
        setTimeout: "readonly",
        window: "readonly"
      }
    }
  }
);
