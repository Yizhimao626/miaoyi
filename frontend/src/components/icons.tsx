import type { SVGProps } from 'react'

/**
 * 统一线性图标集（P1 视觉收敛）
 * —— 24×24 viewBox，stroke 跟随 currentColor，尺寸通过 size 传入。
 * 目的：替换散落在侧栏/顶栏/输入栏的 emoji 伪图标，保证线宽、圆角、颜色一致。
 */
const PATHS = {
  user: (
    <>
      <circle cx="12" cy="8" r="3.6" />
      <path d="M5 20c.8-3.4 3.6-5.4 7-5.4s6.2 2 7 5.4" />
    </>
  ),
  trendingUp: (
    <>
      <path d="M3 17l5.5-5.5 4 4L21 7" />
      <path d="M15 7h6v6" />
    </>
  ),
  brain: (
    <>
      <path d="M12 5a4 4 0 0 0-4 4c-1.8.4-3 1.8-3 3.6 0 1.5.9 2.9 2.2 3.5-.1.4-.2.9-.2 1.3A3.6 3.6 0 0 0 10.6 21c.6 0 1-.4 1-1V6.8A3.8 3.8 0 0 0 12 5Z" />
      <path d="M12 5a4 4 0 0 1 4 4c1.8.4 3 1.8 3 3.6 0 1.5-.9 2.9-2.2 3.5.1.4.2.9.2 1.3A3.6 3.6 0 0 1 13.4 21c-.6 0-1-.4-1-1V6.8A3.8 3.8 0 0 1 12 5Z" />
    </>
  ),
  book: (
    <>
      <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
      <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2Z" />
    </>
  ),
  settings: (
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M12.2 2h-.4a2 2 0 0 0-2 2v.2a2 2 0 0 1-1 1.7l-.4.3a2 2 0 0 1-2 0l-.2-.1a2 2 0 0 0-2.7.7l-.2.4a2 2 0 0 0 .7 2.7l.2.1a2 2 0 0 1 1 1.7v.5a2 2 0 0 1-1 1.7l-.2.1a2 2 0 0 0-.7 2.7l.2.4a2 2 0 0 0 2.7.7l.2-.1a2 2 0 0 1 2 0l.4.3a2 2 0 0 1 1 1.7V20a2 2 0 0 0 2 2h.4a2 2 0 0 0 2-2v-.2a2 2 0 0 1 1-1.7l.4-.3a2 2 0 0 1 2 0l.2.1a2 2 0 0 0 2.7-.7l.2-.4a2 2 0 0 0-.7-2.7l-.2-.1a2 2 0 0 1-1-1.7v-.5a2 2 0 0 1 1-1.7l.2-.1a2 2 0 0 0 .7-2.7l-.2-.4a2 2 0 0 0-2.7-.7l-.2.1a2 2 0 0 1-2 0l-.4-.3a2 2 0 0 1-1-1.7V4a2 2 0 0 0-2-2Z" />
    </>
  ),
  info: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 16v-5" />
      <path d="M12 8h.01" />
    </>
  ),
  x: (
    <>
      <path d="M6 6l12 12" />
      <path d="M18 6L6 18" />
    </>
  ),
  cat: (
    <>
      <path d="M4.5 9.5 3 4l5 2a7.5 7.5 0 0 1 8 0l5-2-1.5 5.5" />
      <path d="M20.5 12.5a8.5 7 0 1 1-17 0 8.5 8.5 0 0 1 17 0Z" />
      <path d="M9 13.5c.4.9 1.6 1.5 3 1.5s2.6-.6 3-1.5" />
      <path d="M9.2 10.4h.01M14.8 10.4h.01" strokeWidth="2.4" />
    </>
  ),
  paw: (
    <>
      <ellipse cx="6.2" cy="9" rx="1.8" ry="2.2" />
      <ellipse cx="10.3" cy="6.4" rx="1.8" ry="2.2" />
      <ellipse cx="14.6" cy="6.4" rx="1.8" ry="2.2" />
      <ellipse cx="18.2" cy="9" rx="1.8" ry="2.2" />
      <path d="M12.2 11.6c3.2 0 5.9 2.2 5.9 4.6 0 2-1.6 3.4-3.6 3.4-.9 0-1.6-.4-2.3-.4s-1.4.4-2.3.4c-2 0-3.6-1.4-3.6-3.4 0-2.4 2.7-4.6 5.9-4.6Z" />
    </>
  ),
  fish: (
    <>
      <path d="M4 12c2.6-3.2 5.8-4.8 9.2-4.8 3.8 0 6.3 2 6.8 4.8-.5 2.8-3 4.8-6.8 4.8-3.4 0-6.6-1.6-9.2-4.8Z" />
      <path d="M4 12L2.5 9v6L4 12Z" />
      <circle cx="16.3" cy="10.6" r="0.9" fill="currentColor" stroke="none" />
    </>
  ),
  menu: (
    <>
      <path d="M4 7h16" />
      <path d="M4 12h16" />
      <path d="M4 17h16" />
    </>
  ),
  search: (
    <>
      <circle cx="11" cy="11" r="7" />
      <path d="M20 20l-3.5-3.5" />
    </>
  ),
  chevronLeft: <path d="M15 6l-6 6 6 6" />,
  mic: (
    <>
      <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
      <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
      <path d="M12 19v3" />
    </>
  ),
  smile: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M8 14s1.5 2 4 2 4-2 4-2" />
      <path d="M9 9h.01M15 9h.01" strokeWidth="2.4" />
    </>
  ),
  arrowUp: (
    <>
      <path d="M12 20V5" />
      <path d="M6 11l6-6 6 6" />
    </>
  ),
  image: (
    <>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <circle cx="8.5" cy="9.5" r="1.5" />
      <path d="M21 15l-5-5L5 20" />
    </>
  ),
  camera: (
    <>
      <path d="M21 19a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V9a2 2 0 0 1 2-2h3l2-3h4l2 3h3a2 2 0 0 1 2 2Z" />
      <circle cx="12" cy="13" r="3.6" />
    </>
  ),
  paperclip: <path d="M20.5 11.5l-8.2 8.2a5.3 5.3 0 0 1-7.5-7.5l8.2-8.2a3.5 3.5 0 0 1 5 5l-8.2 8.2a1.8 1.8 0 0 1-2.5-2.5l7.5-7.5" />,
  palette: (
    <>
      <path d="M12 21.5a9.5 9.5 0 1 1 9.5-9.5c0 2.1-1.7 3.8-3.8 3.8h-1.4a1.9 1.9 0 0 0-1.3 3.3l.4.4a1.7 1.7 0 0 1-1.4 2.9Z" />
      <path d="M8 9h.01M12 7h.01M16 9h.01M7.5 13.5h.01" strokeWidth="2.4" />
    </>
  ),
  headphones: (
    <>
      <path d="M4 14v-2a8 8 0 0 1 16 0v2" />
      <rect x="3" y="14" width="4.5" height="7" rx="2" />
      <rect x="16.5" y="14" width="4.5" height="7" rx="2" />
    </>
  ),
  phone: <path d="M7.5 3h2l1.5 5-2 1.5a12 12 0 0 0 5.5 5.5L16 13l5 1.5v2a3 3 0 0 1-3.3 3A16.5 16.5 0 0 1 4 6.3 3 3 0 0 1 7 3Z" />,
  reply: (
    <>
      <path d="M9 17l-5-5 5-5" />
      <path d="M20 19v-2a6 6 0 0 0-6-6H4" />
    </>
  ),
  check: <path d="M5 13l4 4L19 7" />,
  alert: (
    <>
      <path d="M12 3.5 21 19.5H3L12 3.5Z" />
      <path d="M12 10v4" />
      <path d="M12 17.2h.01" strokeWidth="2.4" />
    </>
  ),
  share: (
    <>
      <path d="M12 15V4" />
      <path d="M8 8l4-4 4 4" />
      <path d="M5 13v6a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-6" />
    </>
  ),
  copy: (
    <>
      <rect x="9" y="9" width="12" height="12" rx="2" />
      <path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1" />
    </>
  ),
  refresh: (
    <>
      <path d="M20.5 12a8.5 8.5 0 1 1-2.6-6.1" />
      <path d="M20.5 4.5V10h-5.4" />
    </>
  ),
  bell: (
    <>
      <path d="M18 8.5a6 6 0 0 0-12 0c0 6.5-2.5 8-2.5 8h17s-2.5-1.5-2.5-8" />
      <path d="M13.7 20.5a2 2 0 0 1-3.4 0" />
    </>
  ),
  sun: (
    <>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M19.1 4.9l-1.4 1.4M6.3 17.7l-1.4 1.4" />
    </>
  ),
  smartphone: (
    <>
      <rect x="6.5" y="2.5" width="11" height="19" rx="2.2" />
      <path d="M10.5 18.5h3" />
    </>
  ),
  messageSquare: (
    <>
      <path d="M20.5 11.5a7.5 7.5 0 0 1-7.5 7.5H8l-4.5 3v-4.3A7.5 7.5 0 0 1 3.5 12 7.5 7.5 0 0 1 11 4.5h2a7.5 7.5 0 0 1 7.5 7Z" />
    </>
  ),
  eye: (
    <>
      <path d="M2.5 12S5.5 5.5 12 5.5 21.5 12 21.5 12 18.5 18.5 12 18.5 2.5 12 2.5 12Z" />
      <circle cx="12" cy="12" r="3" />
    </>
  ),
  download: (
    <>
      <path d="M12 4v11" />
      <path d="M8 11l4 4 4-4" />
      <path d="M5 20h14" />
    </>
  ),
  fileText: (
    <>
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8l-5-5Z" />
      <path d="M14 3v5h5" />
      <path d="M9 13h6M9 17h4" />
    </>
  ),
  clipboard: (
    <>
      <rect x="5" y="4.5" width="14" height="16.5" rx="2" />
      <path d="M9 4.5V3.5A1.5 1.5 0 0 1 10.5 2h3A1.5 1.5 0 0 1 15 3.5v1" />
      <path d="M9 10h6M9 14h6" />
    </>
  ),
  puzzle: (
    <>
      <path d="M10 4a2 2 0 1 1 4 0v1.5h3.5a1.5 1.5 0 0 1 1.5 1.5V11h-1.5a2 2 0 1 0 0 4H19v3.5a1.5 1.5 0 0 1-1.5 1.5H14v-1.5a2 2 0 1 0-4 0V20H6.5A1.5 1.5 0 0 1 5 18.5V15H3.5a2 2 0 1 1 0-4H5V7a1.5 1.5 0 0 1 1.5-1.5H10V4Z" />
    </>
  ),
  star: <path d="M12 3.5l2.7 5.5 6 .9-4.35 4.2 1.03 6L12 17.3 6.62 20.1l1.03-6L3.3 9.9l6-.9L12 3.5Z" />,
  clock: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5.2l3.4 2" />
    </>
  ),
  calendar: (
    <>
      <rect x="3.5" y="5" width="17" height="16" rx="2" />
      <path d="M8 3v4M16 3v4" />
      <path d="M3.5 10h17" />
    </>
  ),
  folder: <path d="M3.5 6.5a2 2 0 0 1 2-2h4l2 2.5h7a2 2 0 0 1 2 2v8.5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2V6.5Z" />,
  crown: (
    <>
      <path d="M3 8l3.5 3L12 5l5.5 6L21 8l-1.6 9.5a1.5 1.5 0 0 1-1.5 1.3H6.1a1.5 1.5 0 0 1-1.5-1.3L3 8Z" />
    </>
  ),
  logout: (
    <>
      <path d="M9.5 21H5.5A1.5 1.5 0 0 1 4 19.5v-15A1.5 1.5 0 0 1 5.5 3h4" />
      <path d="M16 17l5-5-5-5" />
      <path d="M21 12H9.5" />
    </>
  ),
  edit: (
    <>
      <path d="M16.5 4.5a2.1 2.1 0 0 1 3 3L9 18l-4.5 1.5L6 15l10.5-10.5Z" />
      <path d="M14.5 6.5l3 3" />
    </>
  ),
  tag: (
    <>
      <path d="M3.5 11.5V5a1.5 1.5 0 0 1 1.5-1.5h6.5L21 13l-7.5 7.5-10-9Z" />
      <circle cx="7.8" cy="7.8" r="1.3" />
    </>
  ),
  pin: (
    <>
      <path d="M9 3.5h6" />
      <path d="M10 3.5v5.2L7 12v2h10v-2l-3-3.3V3.5" />
      <path d="M12 14v6.5" />
    </>
  ),
  gift: (
    <>
      <rect x="3.5" y="8" width="17" height="4" rx="1" />
      <path d="M5 12v7a1.5 1.5 0 0 0 1.5 1.5h11A1.5 1.5 0 0 0 19 19v-7" />
      <path d="M12 8v12.5" />
      <path d="M12 8S10.5 3.5 8 3.5a2.2 2.2 0 0 0 0 4.5h4Z" />
      <path d="M12 8s1.5-4.5 4-4.5a2.2 2.2 0 0 1 0 4.5h-4Z" />
    </>
  ),
  flame: <path d="M12 21c3.9 0 6.5-2.6 6.5-6 0-4.5-4.3-6.5-4.3-11-2.4 1.2-4.2 3.4-4.2 5.6 0 1.4.6 2.4.6 3.4a2.1 2.1 0 0 1-2.1 2.1c-1 0-1.7-.5-2.3-1.3-.7 1.2-1.2 2.5-1.2 3.7 0 3.4 2.7 5.5 7 5.5Z" />,
  chartBar: (
    <>
      <path d="M4 20V4" />
      <path d="M4 20h16" />
      <path d="M8 16v-5M13 16V8M18 16v-3" />
    </>
  ),
  trophy: (
    <>
      <path d="M8 4h8v4a4 4 0 0 1-8 0V4Z" />
      <path d="M8 5H5.5a2.5 2.5 0 0 0 2.5 5" />
      <path d="M16 5h2.5a2.5 2.5 0 0 1-2.5 5" />
      <path d="M12 12v4" />
      <path d="M8.5 20h7" />
      <path d="M10 16h4l1 4H9l1-4Z" />
    </>
  ),
  chevronDown: <path d="M6 9l6 6 6-6" />,
  key: (
    <>
      <circle cx="8" cy="8" r="4" />
      <path d="M11 11l9 9" />
      <path d="M17 17l2-2M14.5 14.5l2-2" />
    </>
  ),
  globe: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M3 12h18" />
      <path d="M12 3a14 14 0 0 1 0 18a14 14 0 0 1 0-18Z" />
    </>
  ),
  cloudRain: (
    <>
      <path d="M7 16a4 4 0 0 1-.6-7.96A5.5 5.5 0 0 1 17 7.2 3.5 3.5 0 0 1 17.5 16H7Z" />
      <path d="M8 19l-1 2M12 19l-1 2M16 19l-1 2" />
    </>
  ),
  play: <path d="M8 5.5l11 6.5-11 6.5v-13Z" />,
  pause: (
    <>
      <path d="M9 5v14" />
      <path d="M15 5v14" />
    </>
  ),
  micOff: (
    <>
      <path d="M15 5v4M9 5v7a3 3 0 0 0 4.5 2.6" />
      <path d="M19 10v2a7 7 0 0 1-1 3.5" />
      <path d="M12 19v3" />
      <path d="M3 3l18 18" />
    </>
  ),
  phoneOff: (
    <>
      <path d="M7.5 3h2l1.5 5-2 1.5a12 12 0 0 0 5.5 5.5L16 13l5 1.5v2a3 3 0 0 1-3.3 3A16.5 16.5 0 0 1 4 6.3 3 3 0 0 1 7 3Z" />
      <path d="M3 3l18 18" />
    </>
  ),
  heart: <path d="M20.8 5.6a5.5 5.5 0 0 0-7.8 0L12 6.6l-1-1a5.5 5.5 0 0 0-7.8 7.8l8.8 8.8 8.8-8.8a5.5 5.5 0 0 0 0-7.8Z" />,
  mail: (
    <>
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <path d="M3 7.5l9 6 9-6" />
    </>
  ),
  volume: (
    <>
      <path d="M11 5L6 9H3v6h3l5 4V5Z" />
      <path d="M15.5 8.5a5 5 0 0 1 0 7" />
    </>
  ),
  volumeOff: (
    <>
      <path d="M11 5L6 9H3v6h3l5 4V5Z" />
      <path d="M16 9.5l5 5" />
      <path d="M21 9.5l-5 5" />
    </>
  ),
  moon: <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" />,
  lock: (
    <>
      <rect x="4.5" y="10.5" width="15" height="10.5" rx="2.2" />
      <path d="M8 10.5V7a4 4 0 0 1 8 0v3.5" />
    </>
  ),
  sparkles: (
    <>
      <path d="M12 3l1.8 4.7 4.7 1.8-4.7 1.8L12 16l-1.8-4.7L5.5 9.5l4.7-1.8L12 3Z" />
      <path d="M19 15l.9 2.2 2.1.8-2.1.8L19 21l-.9-2.2-2.1-.8 2.1-.8L19 15Z" />
    </>
  ),
  list: (
    <>
      <path d="M9 6h11" />
      <path d="M9 12h11" />
      <path d="M9 18h11" />
      <path d="M4.5 6h.01M4.5 12h.01M4.5 18h.01" strokeWidth="2.4" />
    </>
  ),
  shield: (
    <>
      <path d="M12 3l7.5 3v5.5c0 4.3-3.1 8-7.5 9.5-4.4-1.5-7.5-5.2-7.5-9.5V6L12 3Z" />
      <path d="M9 12l2 2 4-4" />
    </>
  ),
  trash: (
    <>
      <path d="M4 6h16" />
      <path d="M9 6V4.5A1.5 1.5 0 0 1 10.5 3h3A1.5 1.5 0 0 1 15 4.5V6" />
      <path d="M18.5 6l-1 13a2 2 0 0 1-2 2H8.5a2 2 0 0 1-2-2L5.5 6" />
    </>
  ),
  wifiOff: (
    <>
      <path d="M3 8.8a16 16 0 0 1 5.4-2.8" />
      <path d="M2 13.2a11 11 0 0 1 4.6-2.3" />
      <path d="M8.4 17.2a6 6 0 0 1 7.2 0" />
      <path d="M12 21h.01" />
      <path d="M3 3l18 18" />
    </>
  ),
  type: (
    <>
      <path d="M4 6V4h16v2" />
      <path d="M12 4v16" />
      <path d="M9 20h6" />
    </>
  ),
  activity: <path d="M3 12h4l3 8 4-16 3 8h4" />,
  link: (
    <>
      <path d="M10 13a5 5 0 0 0 7.1 0l2-2a5 5 0 0 0-7.1-7.1l-1.2 1.2" />
      <path d="M14 11a5 5 0 0 0-7.1 0l-2 2a5 5 0 0 0 7.1 7.1l1.2-1.2" />
    </>
  ),
}

export type IconName = keyof typeof PATHS

interface IconProps extends Omit<SVGProps<SVGSVGElement>, 'children' | 'name'> {
  name: IconName
  size?: number
}

export function Icon({ name, size = 20, strokeWidth, ...rest }: IconProps) {
  // 【图标质感】小尺寸线性图标按比例加粗描边，避免在手机高分屏上显得又细又虚；
  // 大尺寸保持轻盈。显式传 strokeWidth 时以调用方为准。
  const sw = strokeWidth ?? (size <= 16 ? 2.1 : size <= 20 ? 1.95 : 1.8)
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth={sw}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      {PATHS[name]}
    </svg>
  )
}
