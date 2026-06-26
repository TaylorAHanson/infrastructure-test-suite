/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: {
          DEFAULT: '#0b1220',
          soft: '#1b2435',
        },
        panel: '#111a2b',
        edge: '#22304a',
        accent: '#ff6b3d', // Databricks-ish lava
        muted: '#8a97ad',
        pass: '#3fb950',
        fail: '#f85149',
        warn: '#d29922',
        skip: '#6e7681',
        info: '#58a6ff',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
    },
  },
  plugins: [],
}
