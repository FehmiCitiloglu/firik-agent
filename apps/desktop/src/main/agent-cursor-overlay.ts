import type { BrowserPointerState } from '@firik/contracts/desktop-api';

export interface CursorOverlayLayout {
  visible: boolean;
  obscured: boolean;
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface ElectronImageViewLike {
  setImage(image: unknown): void;
  setBackgroundColor?(color: string): void;
  setBounds(bounds: { x: number; y: number; width: number; height: number }): void;
  setVisible(visible: boolean): void;
  getVisible?(): boolean;
}

export interface CursorOverlayParentWindowLike {
  isDestroyed(): boolean;
  contentView: {
    children?: unknown[];
    addChildView(view: unknown, index?: number): void;
    removeChildView(view: unknown): void;
  };
}

export interface AgentCursorOverlayOptions {
  getParentWindow?: () => CursorOverlayParentWindowLike | null;
  ImageView?: new () => ElectronImageViewLike;
  createImageFromBitmap?: (
    buffer: Buffer,
    options: { width: number; height: number; scaleFactor: number },
  ) => unknown;
  idleHideMs?: number;
  hoverHideMs?: number;
}

type CursorVariant = 'default' | 'hover' | 'clicking';

const SPRITE_CSS_SIZE = 36;
const SPRITE_PX_SIZE = 72; // 2x HiDPI bitmap
const HOTSPOT_CSS_X = 12;
const HOTSPOT_CSS_Y = 12;
const HOTSPOT_PX_X = HOTSPOT_CSS_X * 2;
const HOTSPOT_PX_Y = HOTSPOT_CSS_Y * 2;

const POINTER_POLY: readonly [number, number][] = [
  [HOTSPOT_PX_X, HOTSPOT_PX_Y],
  [HOTSPOT_PX_X, HOTSPOT_PX_Y + 34],
  [HOTSPOT_PX_X + 8, HOTSPOT_PX_Y + 25],
  [HOTSPOT_PX_X + 14, HOTSPOT_PX_Y + 39],
  [HOTSPOT_PX_X + 19, HOTSPOT_PX_Y + 36],
  [HOTSPOT_PX_X + 13, HOTSPOT_PX_Y + 22],
  [HOTSPOT_PX_X + 25, HOTSPOT_PX_Y + 22],
];

function pointToSegmentDist(px: number, py: number, ax: number, ay: number, bx: number, by: number): number {
  const vx = bx - ax;
  const vy = by - ay;
  const wx = px - ax;
  const wy = py - ay;
  const lenSq = vx * vx + vy * vy;
  const t = lenSq > 0 ? Math.max(0, Math.min(1, (wx * vx + wy * vy) / lenSq)) : 0;
  return Math.hypot(px - (ax + t * vx), py - (ay + t * vy));
}

function pointInPolygon(px: number, py: number, poly: readonly [number, number][]): boolean {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i];
    const [xj, yj] = poly[j];
    const intersects = yi > py !== yj > py && px < ((xj - xi) * (py - yi)) / (yj - yi + 1e-9) + xi;
    if (intersects) inside = !inside;
  }
  return inside;
}

function signedPolygonDistance(px: number, py: number, poly: readonly [number, number][]): number {
  let minDist = Infinity;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const d = pointToSegmentDist(px, py, poly[j][0], poly[j][1], poly[i][0], poly[i][1]);
    if (d < minDist) minDist = d;
  }
  return pointInPolygon(px, py, poly) ? -minDist : minDist;
}

function clamp01(v: number): number {
  return Math.max(0, Math.min(1, v));
}

function smoothstep(edge0: number, edge1: number, x: number): number {
  const t = clamp01((x - edge0) / (edge1 - edge0));
  return t * t * (3 - 2 * t);
}

function blendOver(
  dst: [number, number, number, number],
  r: number,
  g: number,
  b: number,
  a: number,
): void {
  if (a <= 0) return;
  const srcA = clamp01(a);
  const invA = 1 - srcA;
  const outA = srcA + dst[3] * invA;
  if (outA <= 0) return;
  dst[0] = (r * srcA + dst[0] * dst[3] * invA) / outA;
  dst[1] = (g * srcA + dst[1] * dst[3] * invA) / outA;
  dst[2] = (b * srcA + dst[2] * dst[3] * invA) / outA;
  dst[3] = outA;
}

function renderCursorBitmap(variant: CursorVariant): Buffer {
  const buf = Buffer.alloc(SPRITE_PX_SIZE * SPRITE_PX_SIZE * 4, 0);
  for (let py = 0; py < SPRITE_PX_SIZE; py++) {
    for (let px = 0; px < SPRITE_PX_SIZE; px++) {
      const pixel: [number, number, number, number] = [0, 0, 0, 0];
      const cx = px + 0.5;
      const cy = py + 0.5;
      const rTip = Math.hypot(cx - HOTSPOT_PX_X, cy - HOTSPOT_PX_Y);

      if (variant === 'hover') {
        const outer = 1 - smoothstep(11, 14, rTip);
        const inner = 1 - smoothstep(6, 9, rTip);
        const ringAlpha = Math.max(0, outer - inner * 0.45) * 0.42;
        blendOver(pixel, 20, 184, 166, ringAlpha);
      } else if (variant === 'clicking') {
        const ringOuter = 1 - smoothstep(16, 19.5, rTip);
        const ringInner = smoothstep(10.5, 13, rTip);
        blendOver(pixel, 16, 185, 129, ringOuter * ringInner * 0.88);
        const halo = (1 - smoothstep(11, 15, rTip)) * 0.25;
        blendOver(pixel, 255, 255, 255, halo);
      }

      const sdShadow = signedPolygonDistance(cx - 2.0, cy - 2.5, POINTER_POLY);
      const shadowCov = 1 - smoothstep(1.2, 5.2, sdShadow);
      blendOver(pixel, 10, 14, 19, shadowCov * 0.36);

      const sd = signedPolygonDistance(cx, cy, POINTER_POLY);
      const outlineCov = 1 - smoothstep(2.0, 3.3, sd);
      blendOver(pixel, 255, 255, 255, outlineCov);

      const fillCov = 1 - smoothstep(-0.6, 0.7, sd);
      const shade = clamp01((cy - HOTSPOT_PX_Y) / 40);
      const fillR = variant === 'clicking' ? 52 : Math.round(20 - shade * 7);
      const fillG = variant === 'clicking' ? 211 : Math.round(184 - shade * 36);
      const fillB = variant === 'clicking' ? 153 : Math.round(166 - shade * 30);
      blendOver(pixel, fillR, fillG, fillB, fillCov);

      const a = Math.max(0, Math.min(255, Math.round(pixel[3] * 255)));
      if (a > 0) {
        const idx = (py * SPRITE_PX_SIZE + px) * 4;
        buf[idx] = Math.round((pixel[2] * a) / 255);
        buf[idx + 1] = Math.round((pixel[1] * a) / 255);
        buf[idx + 2] = Math.round((pixel[0] * a) / 255);
        buf[idx + 3] = a;
      }
    }
  }
  return buf;
}

/**
 * Renders an OS-composited transparent `ImageView` agent cursor above the active
 * `WebContentsView` without mutating visited website DOM or screenshots.
 */
export class AgentCursorOverlay {
  private readonly options: AgentCursorOverlayOptions;
  private view: ElectronImageViewLike | null = null;
  private currentVariant: CursorVariant | null = null;
  private sprites: Record<CursorVariant, unknown> | null = null;
  private idleTimer: NodeJS.Timeout | null = null;
  private unavailable = false;

  constructor(options: AgentCursorOverlayOptions = {}) {
    this.options = options;
  }

  private resolveApis(): {
    ImageView: new () => ElectronImageViewLike;
    createImageFromBitmap: (
      buffer: Buffer,
      options: { width: number; height: number; scaleFactor: number },
    ) => unknown;
  } | null {
    if (this.unavailable) return null;
    if (this.options.ImageView && this.options.createImageFromBitmap) {
      return {
        ImageView: this.options.ImageView,
        createImageFromBitmap: this.options.createImageFromBitmap,
      };
    }
    try {
      // eslint-disable-next-line @typescript-eslint/no-require-imports, @typescript-eslint/no-explicit-any
      const electron = require('electron') as any;
      const ImageViewCtor = this.options.ImageView ?? electron.ImageView;
      const createBitmap =
        this.options.createImageFromBitmap ??
        (electron.nativeImage?.createFromBitmap
          ? electron.nativeImage.createFromBitmap.bind(electron.nativeImage)
          : undefined);
      if (typeof ImageViewCtor !== 'function' || typeof createBitmap !== 'function') {
        this.unavailable = true;
        return null;
      }
      return { ImageView: ImageViewCtor, createImageFromBitmap: createBitmap };
    } catch {
      this.unavailable = true;
      return null;
    }
  }

  private ensureSprites(
    createImageFromBitmap: (
      buffer: Buffer,
      options: { width: number; height: number; scaleFactor: number },
    ) => unknown,
  ): Record<CursorVariant, unknown> {
    if (this.sprites) return this.sprites;
    const opts = { width: SPRITE_PX_SIZE, height: SPRITE_PX_SIZE, scaleFactor: 2 };
    this.sprites = {
      default: createImageFromBitmap(renderCursorBitmap('default'), opts),
      hover: createImageFromBitmap(renderCursorBitmap('hover'), opts),
      clicking: createImageFromBitmap(renderCursorBitmap('clicking'), opts),
    };
    return this.sprites;
  }

  private clearIdleTimer(): void {
    if (this.idleTimer) {
      clearTimeout(this.idleTimer);
      this.idleTimer = null;
    }
  }

  show(pointer: BrowserPointerState, layout: CursorOverlayLayout, zoomFactor = 1): void {
    if (!pointer.visible || !layout.visible || layout.obscured || layout.width < 20 || layout.height < 20) {
      this.hide();
      return;
    }
    const parent = this.options.getParentWindow?.();
    if (!parent || parent.isDestroyed()) {
      this.hide();
      return;
    }
    const apis = this.resolveApis();
    if (!apis) return;

    const sprites = this.ensureSprites(apis.createImageFromBitmap);
    if (!this.view) {
      this.view = new apis.ImageView();
      this.view.setBackgroundColor?.('#00000000');
    }

    const variant: CursorVariant = pointer.pressing
      ? 'clicking'
      : pointer.action === 'hover'
        ? 'hover'
        : 'default';
    if (this.currentVariant !== variant) {
      this.view.setImage(sprites[variant]);
      this.currentVariant = variant;
    }

    const scale = Number.isFinite(zoomFactor) && zoomFactor > 0 ? zoomFactor : 1;
    const rawX = Math.round(layout.x + pointer.x * scale - HOTSPOT_CSS_X);
    const rawY = Math.round(layout.y + pointer.y * scale - HOTSPOT_CSS_Y);
    const minX = Math.round(layout.x - HOTSPOT_CSS_X + 2);
    const maxX = Math.round(layout.x + Math.max(1, layout.width) - SPRITE_CSS_SIZE + 12);
    const minY = Math.round(layout.y - HOTSPOT_CSS_Y + 2);
    const maxY = Math.round(layout.y + Math.max(1, layout.height) - SPRITE_CSS_SIZE + 12);
    const clampedX = Math.max(minX, Math.min(Math.max(minX, maxX), rawX));
    const clampedY = Math.max(minY, Math.min(Math.max(minY, maxY), rawY));

    this.view.setBounds({ x: clampedX, y: clampedY, width: SPRITE_CSS_SIZE, height: SPRITE_CSS_SIZE });
    this.view.setVisible(true);
    // Re-adding ensures cursor sits above the active WebContentsView in z-order
    parent.contentView.addChildView(this.view);

    this.clearIdleTimer();
    const delayMs =
      pointer.action === 'hover'
        ? Math.max(10, this.options.hoverHideMs ?? 2400)
        : Math.max(10, this.options.idleHideMs ?? 1700);
    this.idleTimer = setTimeout(() => {
      this.hide();
    }, delayMs);
    this.idleTimer.unref?.();
  }

  hide(): void {
    this.clearIdleTimer();
    if (!this.view) return;
    try {
      this.view.setVisible(false);
      const parent = this.options.getParentWindow?.();
      if (parent && !parent.isDestroyed()) {
        parent.contentView.removeChildView(this.view);
      }
    } catch {
      // Ignore already detached cursor view
    }
  }

  destroy(): void {
    this.hide();
    this.view = null;
    this.sprites = null;
    this.currentVariant = null;
  }
}
