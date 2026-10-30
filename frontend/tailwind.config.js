import frappeUIPreset, { content as frappeUIContent } from 'frappe-ui/tailwind'

export default {
  presets: [frappeUIPreset],
  content: ['./index.html', './src/**/*.{vue,js,ts}', ...frappeUIContent],
  theme: {
    extend: {
      // Polaris design system tokens: canvas background, subdued borders, refined typography.
      colors: {
        desk: { bg: '#f1f2f4', border: '#e1e3e5' },
        polaris: {
          canvas: '#f1f2f4',
          surface: '#ffffff',
          border: '#e1e3e5',
          'border-hover': '#c9cccf',
          text: '#1a1a1a',
          secondary: '#616161',
          subdued: '#8c9196',
          hover: '#f7f7f8',
          active: '#ebebeb',
          primary: '#1a1a1a',
          'primary-hover': '#303030',
          focus: '#005bd3',
          success: '#0d5936',
          'success-bg': '#e4f6ed',
          'success-border': '#aee8cb',
          attention: '#744210',
          'attention-bg': '#fef3d6',
          'attention-border': '#fbd88e',
          critical: '#9b1c1c',
          'critical-bg': '#fde8e8',
          'critical-border': '#f8b4b4',
          info: '#0369a1',
          'info-bg': '#e0f2fe',
          'info-border': '#bae6fd',
        },
      },
      boxShadow: {
        polaris: '0 1px 3px 0 rgba(0, 0, 0, 0.05), 0 1px 2px 0 rgba(0, 0, 0, 0.03)',
        'polaris-hover': '0 4px 6px -1px rgba(0, 0, 0, 0.07), 0 2px 4px -1px rgba(0, 0, 0, 0.04)',
      },
      borderRadius: {
        xs: '4px',
        sm: '6px',
        md: '8px',
        lg: '10px',
        xl: '12px',
        '2xl': '16px',
      },
      width: { sidebar: '240px' },
      height: { topbar: '48px' },
    },
  },
}
