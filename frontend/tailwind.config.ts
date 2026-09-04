import type { Config } from 'tailwindcss';

const config: Config = {
  content: ['./app/**/*.{ts,tsx}', './components/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        paper: 'var(--paper)',
        surface: 'var(--surface)',
        ink: 'var(--ink)',
        'ink-soft': 'var(--ink-soft)',
        'ink-faint': 'var(--ink-faint)',
        rule: 'var(--rule)',
        accent: 'var(--accent)',
        high: 'var(--high)',
        meaningful: 'var(--meaningful)',
        watch: 'var(--watch)',
      },
      fontFamily: {
        display: 'var(--font-display)',
        sans: 'var(--font-sans)',
      },
      maxWidth: { reading: '64ch' },
    },
  },
  plugins: [],
};
export default config;
