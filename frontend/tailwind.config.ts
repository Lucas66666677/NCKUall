import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        ink: "#17211f",
        campus: "#0f766e",
        brick: "#b45309",
        mist: "#f5f7f7",
      },
      boxShadow: {
        soft: "0 18px 45px rgba(23, 33, 31, 0.12)",
      },
    },
  },
  plugins: [],
};

export default config;
