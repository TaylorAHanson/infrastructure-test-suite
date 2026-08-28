/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      // Colours resolve against the branding theme's CSS custom properties (see
      // src/index.css), so the shell can repaint the app at runtime (ADR-0013).
      // The `<alpha-value>` placeholder keeps Tailwind's `/opacity` modifiers
      // (e.g. `bg-info/15`) working against the "r g b" channel vars.
      colors: {
        ink: {
          DEFAULT: 'rgb(var(--brand-ink) / <alpha-value>)',
          soft: 'rgb(var(--brand-ink_soft) / <alpha-value>)',
        },
        panel: 'rgb(var(--brand-panel) / <alpha-value>)',
        edge: 'rgb(var(--brand-edge) / <alpha-value>)',
        accent: 'rgb(var(--brand-accent) / <alpha-value>)', // Databricks-ish lava
        muted: 'rgb(var(--brand-muted) / <alpha-value>)',
        pass: 'rgb(var(--brand-pass) / <alpha-value>)',
        fail: 'rgb(var(--brand-fail) / <alpha-value>)',
        warn: 'rgb(var(--brand-warn) / <alpha-value>)',
        skip: 'rgb(var(--brand-skip) / <alpha-value>)',
        info: 'rgb(var(--brand-info) / <alpha-value>)',
        flaky: 'rgb(var(--brand-flaky) / <alpha-value>)', // advisory / non-deterministic (ADR-0008)
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
    },
  },
  plugins: [],
}
