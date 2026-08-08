import type { SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement>;
const base = { width: 21, height: 21, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.6, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };

export const SearchIcon = (props: IconProps) => <svg {...base} {...props}><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/></svg>;
export const DriveIcon = (props: IconProps) => <svg {...base} {...props}><path d="M4 14 6.5 5h11L20 14"/><rect x="4" y="14" width="16" height="5" rx="1"/><path d="M16 16.5h.01"/></svg>;
export const DownloadIcon = (props: IconProps) => <svg {...base} {...props}><path d="M12 3v12m-4-4 4 4 4-4"/><path d="M4 18v2h16v-2"/></svg>;
export const ToolIcon = (props: IconProps) => <svg {...base} {...props}><path d="m14.5 6.5 3-3a4 4 0 0 1-5 5l-7 7a2 2 0 1 0 3 3l7-7a4 4 0 0 1 5-5l-3 3z"/></svg>;
export const CloseIcon = (props: IconProps) => <svg {...base} {...props}><path d="m6 6 12 12M18 6 6 18"/></svg>;
export const CheckIcon = (props: IconProps) => <svg {...base} {...props}><circle cx="12" cy="12" r="9"/><path d="m8 12 2.5 2.5L16 9"/></svg>;
export const PauseIcon = (props: IconProps) => <svg {...base} {...props}><path d="M9 6v12M15 6v12"/></svg>;
export const PlayIcon = (props: IconProps) => <svg {...base} {...props}><path d="m9 6 9 6-9 6z"/></svg>;
export const SparkIcon = (props: IconProps) => <svg {...base} {...props}><path d="M12 3v18M3 12h18M6 6l12 12M18 6 6 18"/></svg>;
export const ArrowIcon = (props: IconProps) => <svg {...base} {...props}><path d="m9 18 6-6-6-6"/></svg>;

export function MarkIcon(props: IconProps) {
  return <svg {...base} {...props} viewBox="0 0 28 28"><rect x="2.5" y="2.5" width="23" height="23" rx="6"/><circle cx="9" cy="9" r="1.5"/><circle cx="19" cy="9" r="1.5"/><circle cx="9" cy="19" r="1.5"/><path d="M10.5 9h7M9 10.5v7m1.5 1.5H19v-5"/></svg>;
}
