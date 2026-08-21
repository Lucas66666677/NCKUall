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
        ink: "#15171c",
        campus: "#1d4ed8",
        brick: "#0f766e",
        mist: "#f7f5ef",
      },
      boxShadow: {
        soft: "0 18px 45px rgba(29, 78, 216, 0.1)",
      },
    },
  },
  plugins: [],
};

export default config;
