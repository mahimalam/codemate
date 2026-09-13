export function BrandIcon({ className = '' }: { className?: string }) {
  return <svg className={className} viewBox="0 0 32 32" role="img" aria-label="VexP Code">
    <rect x="1" y="1" width="30" height="30" rx="8" fill="currentColor" opacity=".12" />
    <path d="M7.5 8.25h4.1L16 21.1l4.4-12.85h4.1L18.2 25h-4.4L7.5 8.25Z" fill="currentColor" />
    <path d="m13.9 15.4 2.1-4.2 2.1 4.2-2.1 5.4-2.1-5.4Z" fill="#c2c9ff" />
  </svg>;
}
