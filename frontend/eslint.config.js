import parser from "@typescript-eslint/parser";
import typescript from "@typescript-eslint/eslint-plugin";
import hooks from "eslint-plugin-react-hooks";

export default [
  { ignores: ["dist/**", "node_modules/**"] },
  {
    files: ["**/*.ts", "**/*.tsx"],
    languageOptions: { parser, parserOptions: { ecmaVersion: "latest", sourceType: "module", ecmaFeatures: { jsx: true } } },
    plugins: { "@typescript-eslint": typescript, "react-hooks": hooks },
    rules: { ...typescript.configs.recommended.rules, ...hooks.configs.recommended.rules },
  },
];
