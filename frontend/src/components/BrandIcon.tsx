export function BrandIcon({ className = '' }: { className?: string }) {
  return <svg className={className} viewBox="0 0 32 32" role="img" aria-label="CodeMate">
    <rect x="1" y="1" width="30" height="30" rx="8" fill="currentColor" opacity=".12" />
    <path d="M22.8 9.4a8.9 8.9 0 1 0 0 13.2l-2.3-2.2a5.7 5.7 0 1 1 0-8.8l2.3-2.2Z" fill="currentColor" />
    <path d="m19 12.4 3.6 3.6-3.6 3.6" fill="none" stroke="#c2c9ff" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" />
  </svg>;
}
