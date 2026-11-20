const paths = {
  book: 'M12 7v14M3 18V4h3a6 6 0 0 1 6 3 6 6 0 0 1 6-3h3v14h-3a8 8 0 0 0-6 3 8 8 0 0 0-6-3H3Z',
  external: 'M13 5h6v6m0-6-9 9m9 0v5H5V5h5',
  refresh: 'M20 7v5h-5M4 17v-5h5m-4-4a8 8 0 0 1 14-1l1 2M4 15l1 2a8 8 0 0 0 14-1',
  language: 'M3 5h12M9 2v3m4 0c0 5-4 9-9 12m1-9c1 3 4 6 7 8m1 5 5-12 5 12m-8-4h6',
  mic: 'M9 5a3 3 0 0 1 6 0v6a3 3 0 0 1-6 0V5Zm-4 6a7 7 0 0 0 14 0m-7 7v4m-3 0h6',
  monitor: 'M3 4h18v12H3zM8 21h8m-4-5v5',
  speech: 'M3 10v4m4-7v10m5-14v18m5-14v10m4-7v4',
  network: 'M9 3h6v5H9zM3 16h6v5H3zm12 0h6v5h-6zM6 16v-4h12v4m-6-8v4',
  sliders: 'M3 6h6m4 0h8M3 12h12m4 0h2M3 18h3m4 0h11M9 3v6m6 0v6m-9 0v6',
  terminal: 'm4 6 6 6-6 6m9 0h7',
  folder: 'M3 18V5h6l2 2h10v3M3 18l4-8h15l-3 8H3Z',
} as const

export function Icon({ name }: { name: keyof typeof paths }) {
  return (
    <svg
      className="bl-icon"
      aria-hidden="true"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d={paths[name]} />
    </svg>
  )
}
