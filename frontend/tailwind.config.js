/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        panel: "#0f172a",
        panel2: "#1e293b",
        accent: "#38bdf8",
        alarm: "#f87171",
        ok: "#4ade80",
      },
    },
  },
  plugins: [],
};
