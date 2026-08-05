/** @type {import('tailwindcss').Config} */
export default {
  prefix: "ops-",
  /** 嵌入父页面 / iframe 时避免 Tailwind Preflight 影响全局（与 prefix 组合做样式隔离） */
  corePlugins: {
    preflight: false,
  },
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {},
  },
  plugins: [],
};
