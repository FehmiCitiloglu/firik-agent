import { randomBytes } from 'node:crypto';
import type { BrowserPointerAction } from '@firik/contracts/desktop-api';
import { BrowserError } from '../browser-worker/models.js';
import { redact, sanitizedUrl } from '../browser-worker/security.js';
import {
  POINTER_ACTION_LABELS,
  TARGET_STATE_SCRIPT,
  computePointerTrajectory,
  hashValue,
} from '../browser-worker/shared.js';

export interface CdpPointerEvent {
  x: number;
  y: number;
  action: BrowserPointerAction;
  pressing: boolean;
  label?: string;
}

export interface CdpTransport {
  isAttached(): boolean;
  attach(protocolVersion?: string): void;
  detach(): void;
  sendCommand<T = unknown>(
    method: string,
    params?: Record<string, unknown>,
    sessionId?: string,
  ): Promise<T>;
  on(
    event: 'message',
    listener: (event: unknown, method: string, params: unknown, sessionId?: string) => void,
  ): void;
  on(event: 'detach', listener: (event: unknown, reason: string) => void): void;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  removeListener?(event: string, listener: (...args: any[]) => void): void;
}

export interface CdpRefEntry {
  ref: string;
  backendNodeId: number;
  frameId: string;
  sessionId?: string;
}

export interface CdpFrameInfo {
  frameId: string;
  parentId?: string;
  url: string;
  name: string;
  sessionId?: string;
}

export interface CdpResolvedTarget {
  backendNodeId: number;
  frameId: string;
  sessionId?: string;
  objectId?: string;
}

export interface CdpDiagnosticEntry {
  level?: string;
  type?: string;
  text?: string;
  message?: string;
  url?: string;
  timestamp: number;
}

interface RawAxValue {
  type?: string;
  value?: unknown;
}

interface RawAxProperty {
  name: string;
  value?: RawAxValue;
}

interface RawAxNode {
  nodeId: string;
  ignored?: boolean;
  role?: RawAxValue;
  name?: RawAxValue;
  value?: RawAxValue;
  properties?: RawAxProperty[];
  backendDOMNodeId?: number;
  childIds?: string[];
  parentId?: string;
}

interface RawFrameTreeNode {
  frame: {
    id: string;
    parentId?: string;
    name?: string;
    url: string;
  };
  childFrames?: RawFrameTreeNode[];
}

const TEXT_VALUE_OMITTED_ROLES = new Set([
  'textbox',
  'searchbox',
  'combobox',
  'spinbutton',
]);

const SKIPPED_INLINE_CHILDREN_ROLES = new Set([
  'heading',
  'button',
  'link',
  'menuitem',
  'tab',
  'checkbox',
  'radio',
  'switch',
  'option',
]);

const STRUCTURAL_CONTAINER_ROLES = new Set([
  'RootWebArea',
  'generic',
  'none',
  'presentation',
  'Section',
  'LayoutTable',
  'LayoutTableRow',
  'LayoutTableCell',
]);

const KEY_DEFINITIONS: Record<string, { key: string; code: string; keyCode: number; text?: string }> = {
  Enter: { key: 'Enter', code: 'Enter', keyCode: 13, text: '\r' },
  Tab: { key: 'Tab', code: 'Tab', keyCode: 9 },
  Escape: { key: 'Escape', code: 'Escape', keyCode: 27 },
  Backspace: { key: 'Backspace', code: 'Backspace', keyCode: 8 },
  Delete: { key: 'Delete', code: 'Delete', keyCode: 46 },
  ArrowUp: { key: 'ArrowUp', code: 'ArrowUp', keyCode: 38 },
  ArrowDown: { key: 'ArrowDown', code: 'ArrowDown', keyCode: 40 },
  ArrowLeft: { key: 'ArrowLeft', code: 'ArrowLeft', keyCode: 37 },
  ArrowRight: { key: 'ArrowRight', code: 'ArrowRight', keyCode: 39 },
  Home: { key: 'Home', code: 'Home', keyCode: 36 },
  End: { key: 'End', code: 'End', keyCode: 35 },
  PageUp: { key: 'PageUp', code: 'PageUp', keyCode: 33 },
  PageDown: { key: 'PageDown', code: 'PageDown', keyCode: 34 },
  ' ': { key: ' ', code: 'Space', keyCode: 32, text: ' ' },
  Space: { key: ' ', code: 'Space', keyCode: 32, text: ' ' },
};

const MODIFIER_BITS: Record<string, number> = {
  Alt: 1,
  Control: 2,
  Ctrl: 2,
  Meta: 4,
  Command: 4,
  Cmd: 4,
  Shift: 8,
};

function quoteLabel(text: string): string {
  return JSON.stringify(text.replace(/\s+/g, ' ').trim());
}

/**
 * Wraps Electron's `webContents.debugger` (or any CDP transport) to provide
 * ARIA snapshots, deterministic refs, semantic locator resolution, target
 * state inspection, masked screenshots, and user-gesture input without any
 * open TCP debugging port.
 */
export class CdpPageDriver {
  private readonly transport: CdpTransport;
  private attached = false;
  private enablePromise: Promise<void> | null = null;
  private readonly targetSessions = new Map<string, string>();
  private readonly refsByTab = new Map<string, CdpRefEntry>();
  private consoleSink?: (entry: CdpDiagnosticEntry) => void;
  private errorSink?: (entry: CdpDiagnosticEntry) => void;
  private pointerSink?: (entry: CdpPointerEvent) => void;
  private readonly pointerStepDelayMs: number;
  private pointerPos: { x: number; y: number } = { x: 48, y: 48 };
  private pointerInitialized = false;
  private listenersBound = false;

  private readonly handleMessage = (
    _event: unknown,
    method: string,
    params: unknown,
  ): void => {
    const data = (params ?? {}) as Record<string, unknown>;
    if (method === 'Target.attachedToTarget') {
      const targetInfo = data.targetInfo as { targetId?: string } | undefined;
      const sessionId = typeof data.sessionId === 'string' ? data.sessionId : '';
      if (targetInfo?.targetId && sessionId) {
        this.targetSessions.set(targetInfo.targetId, sessionId);
        void this.enableSessionDomains(sessionId).catch(() => {});
      }
      return;
    }
    if (method === 'Target.detachedFromTarget') {
      const targetId = typeof data.targetId === 'string' ? data.targetId : '';
      if (targetId) this.targetSessions.delete(targetId);
      return;
    }
    if (method === 'Runtime.consoleAPICalled' && this.consoleSink) {
      const level = String(data.type ?? '');
      if (level === 'error' || level === 'warning') {
        const args = Array.isArray(data.args) ? data.args : [];
        const text = args
          .map(item => {
            const arg = item as { value?: unknown; description?: string };
            return arg.value !== undefined ? String(arg.value) : String(arg.description ?? '');
          })
          .join(' ');
        this.consoleSink({ type: level, text: redact(text, 1000), timestamp: Date.now() / 1000 });
      }
      return;
    }
    if (method === 'Runtime.exceptionThrown' && this.errorSink) {
      const details = data.exceptionDetails as { text?: string; exception?: { description?: string } } | undefined;
      const msg = details?.exception?.description || details?.text || 'Uncaught exception';
      this.errorSink({ message: redact(msg, 1000), timestamp: Date.now() / 1000 });
    }
  };

  private readonly handleDetach = (): void => {
    this.attached = false;
    this.enablePromise = null;
    this.targetSessions.clear();
  };

  constructor(
    transport: CdpTransport,
    diagnostics?: {
      onConsole?: (entry: CdpDiagnosticEntry) => void;
      onPageError?: (entry: CdpDiagnosticEntry) => void;
      onPointer?: (entry: CdpPointerEvent) => void;
      pointerStepDelayMs?: number;
    },
  ) {
    this.transport = transport;
    this.consoleSink = diagnostics?.onConsole;
    this.errorSink = diagnostics?.onPageError;
    this.pointerSink = diagnostics?.onPointer;
    this.pointerStepDelayMs = Math.max(0, diagnostics?.pointerStepDelayMs ?? 12);
  }

  private async enableSessionDomains(sessionId?: string): Promise<void> {
    await Promise.allSettled([
      this.transport.sendCommand('Page.enable', {}, sessionId),
      this.transport.sendCommand('DOM.enable', {}, sessionId),
      this.transport.sendCommand('Runtime.enable', {}, sessionId),
      this.transport.sendCommand('Accessibility.enable', {}, sessionId),
      this.transport.sendCommand(
        'Target.setAutoAttach',
        { autoAttach: true, waitForDebuggerOnStart: false, flatten: true },
        sessionId,
      ),
    ]);
  }

  async ensureAttached(): Promise<void> {
    if (this.attached && this.transport.isAttached()) {
      return;
    }
    if (this.enablePromise) {
      return this.enablePromise;
    }
    this.enablePromise = (async () => {
      if (!this.listenersBound) {
        this.transport.on('message', this.handleMessage);
        this.transport.on('detach', this.handleDetach);
        this.listenersBound = true;
      }
      if (!this.transport.isAttached()) {
        this.transport.attach('1.3');
      }
      await this.enableSessionDomains(undefined);
      this.attached = true;
    })().finally(() => {
      this.enablePromise = null;
    });
    return this.enablePromise;
  }

  detach(): void {
    try {
      if (this.transport.isAttached()) {
        this.transport.detach();
      }
    } catch {
      // Ignore already detached debuggers during teardown
    }
    if (this.listenersBound && this.transport.removeListener) {
      this.transport.removeListener('message', this.handleMessage);
      this.transport.removeListener('detach', this.handleDetach);
      this.listenersBound = false;
    }
    this.attached = false;
    this.enablePromise = null;
    this.targetSessions.clear();
    this.refsByTab.clear();
  }

  async setEmulation(options: {
    locale?: string;
    acceptLanguage?: string;
    timezoneId?: string;
    userAgent?: string;
  }): Promise<void> {
    await this.ensureAttached();
    const tasks: Promise<unknown>[] = [];
    if (options.timezoneId) {
      tasks.push(
        this.transport
          .sendCommand('Emulation.setTimezoneOverride', { timezoneId: options.timezoneId })
          .catch(() => {}),
      );
    }
    if (options.locale) {
      tasks.push(
        this.transport
          .sendCommand('Emulation.setLocaleOverride', { locale: options.locale })
          .catch(() => {}),
      );
    }
    if (options.userAgent) {
      tasks.push(
        this.transport
          .sendCommand('Emulation.setUserAgentOverride', {
            userAgent: options.userAgent,
            ...(options.acceptLanguage ? { acceptLanguage: options.acceptLanguage } : {}),
          })
          .catch(() => {}),
      );
    }
    await Promise.all(tasks);
  }

  async listFrames(): Promise<CdpFrameInfo[]> {
    await this.ensureAttached();
    const collected = new Map<string, CdpFrameInfo>();
    const collectTree = (node: RawFrameTreeNode, sessionId?: string): void => {
      const f = node.frame;
      const existing = collected.get(f.id);
      if (!existing || (!existing.sessionId && sessionId)) {
        collected.set(f.id, {
          frameId: f.id,
          parentId: f.parentId,
          url: f.url,
          name: f.name ?? '',
          sessionId,
        });
      }
      for (const child of node.childFrames ?? []) {
        collectTree(child, sessionId);
      }
    };

    const mainTree = await this.transport.sendCommand<{ frameTree: RawFrameTreeNode }>('Page.getFrameTree');
    if (mainTree?.frameTree) {
      collectTree(mainTree.frameTree, undefined);
    }

    for (const sessionId of [...this.targetSessions.values()]) {
      try {
        const subTree = await this.transport.sendCommand<{ frameTree: RawFrameTreeNode }>(
          'Page.getFrameTree',
          {},
          sessionId,
        );
        if (subTree?.frameTree) {
          collectTree(subTree.frameTree, sessionId);
        }
      } catch {
        // Frame target may have navigated or closed while listing
      }
    }

    const frames = [...collected.values()];
    if (frames.length > 32) {
      throw new BrowserError('frame_limit', 'The document exceeds the supported observation frame limit.');
    }
    return frames;
  }

  private async evaluateInFrame<T = unknown>(
    frame: CdpFrameInfo,
    expression: string,
  ): Promise<T> {
    const world = await this.transport.sendCommand<{ executionContextId: number }>(
      'Page.createIsolatedWorld',
      {
        frameId: frame.frameId,
        worldName: '__firik_cdp__',
        grantUniveralAccess: true,
      },
      frame.sessionId,
    );
    const res = await this.transport.sendCommand<{
      result?: { value?: T; objectId?: string; description?: string };
      exceptionDetails?: { text?: string; exception?: { description?: string } };
    }>(
      'Runtime.evaluate',
      {
        contextId: world.executionContextId,
        expression,
        returnByValue: true,
        awaitPromise: true,
      },
      frame.sessionId,
    );
    if (res.exceptionDetails) {
      throw new Error(res.exceptionDetails.exception?.description || res.exceptionDetails.text || 'Evaluation failed');
    }
    return res.result?.value as T;
  }

  private async matchingFrames(frameFilter?: string): Promise<CdpFrameInfo[]> {
    const frames = await this.listFrames();
    if (!frameFilter) return frames;
    const matched: CdpFrameInfo[] = [];
    for (const f of frames) {
      if (f.name === frameFilter || f.url === frameFilter || sanitizedUrl(f.url) === frameFilter) {
        matched.push(f);
        continue;
      }
      try {
        const meta = await this.evaluateInFrame<{ title: string; name: string }>(
          f,
          '({ title: String(window.frameElement?.getAttribute?.("title") || window.frameElement?.getAttribute?.("aria-label") || document.title || ""), name: String(window.name || window.frameElement?.getAttribute?.("name") || "") })',
        );
        if (meta && (meta.title === frameFilter || meta.name === frameFilter)) {
          matched.push(f);
        }
      } catch {
        // Cross-origin frame properties already matched via URL/frame name above
      }
    }
    return matched;
  }

  async digest(frameFilter?: string): Promise<string> {
    const frames = await this.matchingFrames(frameFilter);
    const digests: Record<string, unknown>[] = [];
    for (const frame of frames) {
      try {
        const state = await this.evaluateInFrame<Record<string, unknown>>(
          frame,
          `(() => {
            const inputs = Array.from(document.querySelectorAll('input, textarea, select')).slice(0, 200).map(el => {
              const str = String(el.value || '');
              let h = 2166136261;
              for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619); }
              return {
                tag: el.tagName, type: el.type || '', id: el.id || '',
                name: el.getAttribute('name') || '', checked: !!el.checked,
                selected: el.tagName === 'SELECT' ? Array.from(el.selectedOptions || []).map(o => o.value) : [],
                len: str.length, h: h >>> 0
              };
            });
            return {
              url: location.href,
              title: document.title,
              text: (document.body?.innerText || '').slice(0, 8192),
              count: document.querySelectorAll('*').length,
              inputs
            };
          })()`,
        );
        digests.push(state ?? { url: frame.url, unavailable: true });
      } catch {
        digests.push({ url: frame.url, unavailable: true });
      }
    }
    return hashValue(digests);
  }

  async ariaSnapshot(maxChars: number): Promise<{
    content: string;
    truncated: boolean;
    frames: { url: string }[];
    digest: string;
  }> {
    const before = await this.digest();
    const frames = await this.listFrames();
    const mainFrame = frames.find(f => !f.parentId) ?? frames[0];
    if (!mainFrame) {
      throw new BrowserError('browser_operation_failed', 'Active frame is unavailable.');
    }

    const lines: string[] = [];
    const nextRefs = new Map<string, CdpRefEntry>();
    let refSeq = 0;
    const allocRef = (backendNodeId: number, frameId: string, sessionId?: string): string => {
      const ref = `e${++refSeq}`;
      nextRefs.set(ref, { ref, backendNodeId, frameId, sessionId });
      return ref;
    };

    const renderedFrameIds = new Set<string>();
    const childFramesByParent = new Map<string, CdpFrameInfo[]>();
    for (const f of frames) {
      if (f.parentId) {
        const list = childFramesByParent.get(f.parentId) ?? [];
        list.push(f);
        childFramesByParent.set(f.parentId, list);
      }
    }

    const renderFrameTree = async (frame: CdpFrameInfo, indent: number): Promise<void> => {
      if (renderedFrameIds.has(frame.frameId)) return;
      renderedFrameIds.add(frame.frameId);

      let nodes: RawAxNode[] = [];
      try {
        const ax = await this.transport.sendCommand<{ nodes: RawAxNode[] }>(
          'Accessibility.getFullAXTree',
          { frameId: frame.frameId },
          frame.sessionId,
        );
        nodes = ax?.nodes ?? [];
      } catch {
        try {
          const ax = await this.transport.sendCommand<{ nodes: RawAxNode[] }>(
            'Accessibility.getFullAXTree',
            {},
            frame.sessionId,
          );
          nodes = ax?.nodes ?? [];
        } catch {
          nodes = [];
        }
      }

      const byId = new Map<string, RawAxNode>();
      for (const node of nodes) byId.set(node.nodeId, node);
      const root = nodes.find(n => !n.parentId) ?? nodes[0];
      if (!root) return;

      const iframeQueue = [...(childFramesByParent.get(frame.frameId) ?? [])];
      const visited = new Set<string>();

      const walk = async (node: RawAxNode, depth: number, skipTextMatch?: string): Promise<void> => {
        if (visited.has(node.nodeId)) return;
        visited.add(node.nodeId);

        const role = String(node.role?.value ?? '');
        const rawName = String(node.name?.value ?? '');
        const props = new Map<string, unknown>();
        for (const p of node.properties ?? []) {
          props.set(p.name, p.value?.value);
        }

        if (role === 'InlineTextBox') return;

        if (node.ignored || STRUCTURAL_CONTAINER_ROLES.has(role)) {
          for (const cid of node.childIds ?? []) {
            const child = byId.get(cid);
            if (child) await walk(child, depth, skipTextMatch);
          }
          return;
        }

        if (role === 'StaticText') {
          const cleaned = rawName.trim();
          if (cleaned && cleaned !== skipTextMatch) {
            lines.push(`${'  '.repeat(depth)}- text ${quoteLabel(redact(cleaned, 1000))}`);
          }
          return;
        }

        const isEditable =
          TEXT_VALUE_OMITTED_ROLES.has(role) || props.get('editable') === true;

        if (role === 'Iframe') {
          let subFrame: CdpFrameInfo | undefined;
          if (node.backendDOMNodeId) {
            try {
              const desc = await this.transport.sendCommand<{
                node?: { contentDocument?: { frameId?: string }; frameId?: string };
              }>('DOM.describeNode', { backendNodeId: node.backendDOMNodeId }, frame.sessionId);
              const subId = desc?.node?.contentDocument?.frameId ?? desc?.node?.frameId;
              if (subId) subFrame = frames.find(f => f.frameId === subId);
            } catch {
              // Fall back to ordered child frame matching below
            }
          }
          if (!subFrame) {
            subFrame = iframeQueue.find(cf => !renderedFrameIds.has(cf.frameId));
          }
          const labelPart = rawName ? ` ${quoteLabel(redact(rawName, 400))}` : '';
          const refTag = node.backendDOMNodeId
            ? ` [ref=${allocRef(node.backendDOMNodeId, frame.frameId, frame.sessionId)}]`
            : '';
          lines.push(`${'  '.repeat(depth)}- iframe${labelPart}${refTag}:`);
          const childNodes = (node.childIds ?? []).map(id => byId.get(id)).filter(Boolean) as RawAxNode[];
          if (childNodes.length > 0 && subFrame && !subFrame.sessionId) {
            renderedFrameIds.add(subFrame.frameId);
            for (const c of childNodes) await walk(c, depth + 1);
          } else if (subFrame) {
            await renderFrameTree(subFrame, depth + 1);
          }
          return;
        }

        const attrs: string[] = [];
        if (props.has('level') && typeof props.get('level') === 'number') {
          attrs.push(`[level=${props.get('level')}]`);
        }
        if (props.get('checked') === true || props.get('checked') === 'true') {
          attrs.push('[checked]');
        }
        if (props.get('disabled') === true) {
          attrs.push('[disabled]');
        }
        if (props.get('expanded') === true) {
          attrs.push('[expanded]');
        }
        if (props.get('selected') === true) {
          attrs.push('[selected]');
        }
        if (typeof node.backendDOMNodeId === 'number') {
          attrs.push(`[ref=${allocRef(node.backendDOMNodeId, frame.frameId, frame.sessionId)}]`);
        }

        const labelPart = rawName ? ` ${quoteLabel(redact(rawName, 600))}` : '';
        const attrPart = attrs.length > 0 ? ` ${attrs.join(' ')}` : '';
        const suffix = isEditable ? ': [value omitted]' : '';
        lines.push(`${'  '.repeat(depth)}- ${role}${labelPart}${attrPart}${suffix}`);

        if (isEditable) {
          // Never descend into editable descendants so raw input/textarea text
          // cannot leak as child StaticText nodes.
          return;
        }

        const nextSkip =
          SKIPPED_INLINE_CHILDREN_ROLES.has(role) && rawName ? rawName.trim() : undefined;
        for (const cid of node.childIds ?? []) {
          const child = byId.get(cid);
          if (child) await walk(child, depth + 1, nextSkip);
        }
      };

      await walk(root, indent);
      // Ensure any unvisited child frames still appear in snapshot output.
      for (const cf of iframeQueue) {
        if (!renderedFrameIds.has(cf.frameId)) {
          lines.push(`${'  '.repeat(indent)}- iframe ${quoteLabel(sanitizedUrl(cf.url))}:`);
          await renderFrameTree(cf, indent + 1);
        }
      }
    };

    await renderFrameTree(mainFrame, 0);

    const after = await this.digest();
    if (before !== after) {
      throw new BrowserError(
        'stale_observation',
        'The page changed while its snapshot was captured; take a fresh observation.',
      );
    }

    this.refsByTab.clear();
    for (const [k, v] of nextRefs) {
      this.refsByTab.set(k, v);
    }

    const cleaned = redact(lines.join('\n'), maxChars + 1);
    return {
      content: cleaned.slice(0, maxChars),
      truncated: cleaned.length > maxChars,
      frames: frames.filter(f => f !== mainFrame).map(f => ({ url: sanitizedUrl(f.url) })),
      digest: after,
    };
  }

  async resolveTarget(
    target: Record<string, string>,
    requireReady = true,
  ): Promise<CdpResolvedTarget> {
    await this.ensureAttached();
    const frames = await this.matchingFrames(target.frame);
    if (frames.length === 0) {
      throw new BrowserError('target_missing', 'Requested browser target or frame was not found.');
    }

    if ('ref' in target) {
      const entry = this.refsByTab.get(target.ref);
      if (!entry || !frames.some(f => f.frameId === entry.frameId)) {
        throw new BrowserError('target_missing', 'Requested browser reference is no longer present.');
      }
      let objectId: string | undefined;
      try {
        const resolved = await this.transport.sendCommand<{ object?: { objectId?: string } }>(
          'DOM.resolveNode',
          { backendNodeId: entry.backendNodeId },
          entry.sessionId,
        );
        objectId = resolved?.object?.objectId;
      } catch {
        objectId = undefined;
      }
      if (!objectId) {
        throw new BrowserError('target_missing', 'Requested browser reference is no longer attached.');
      }
      if (requireReady) {
        await this.assertObjectReady(objectId, entry.sessionId);
      }
      return {
        backendNodeId: entry.backendNodeId,
        frameId: entry.frameId,
        sessionId: entry.sessionId,
        objectId,
      };
    }

    const group = `__firik_t_${randomBytes(6).toString('hex')}`;
    const finderExpr = this.buildTargetFinderExpression(target);
    const matches: CdpResolvedTarget[] = [];

    for (const frame of frames) {
      try {
        const world = await this.transport.sendCommand<{ executionContextId: number }>(
          'Page.createIsolatedWorld',
          { frameId: frame.frameId, worldName: '__firik_cdp__', grantUniveralAccess: true },
          frame.sessionId,
        );
        const evalRes = await this.transport.sendCommand<{ result?: { objectId?: string } }>(
          'Runtime.evaluate',
          {
            contextId: world.executionContextId,
            expression: finderExpr,
            objectGroup: group,
            returnByValue: false,
          },
          frame.sessionId,
        );
        const arrayObjId = evalRes?.result?.objectId;
        if (!arrayObjId) continue;

        const props = await this.transport.sendCommand<{
          result?: { name: string; enumerable?: boolean; value?: { objectId?: string } }[];
        }>(
          'Runtime.getProperties',
          { objectId: arrayObjId, ownProperties: true },
          frame.sessionId,
        );

        for (const prop of props.result ?? []) {
          if (!/^\d+$/.test(prop.name) || !prop.value?.objectId) continue;
          const desc = await this.transport.sendCommand<{ node?: { backendNodeId?: number } }>(
            'DOM.describeNode',
            { objectId: prop.value.objectId },
            frame.sessionId,
          );
          if (desc?.node?.backendNodeId) {
            matches.push({
              backendNodeId: desc.node.backendNodeId,
              frameId: frame.frameId,
              sessionId: frame.sessionId,
              objectId: prop.value.objectId,
            });
          }
        }
      } catch {
        // Skip inaccessible or destroyed frames while checking remaining frames
      }
    }

    if (matches.length === 0) {
      await this.releaseGroup(group);
      throw new BrowserError('target_missing', 'Requested browser target was not found.');
    }
    if (matches.length > 1) {
      await this.releaseGroup(group);
      throw new BrowserError('ambiguous_target', 'Browser target matched multiple elements; refine target.');
    }

    const match = matches[0];
    if (requireReady && match.objectId) {
      try {
        await this.assertObjectReady(match.objectId, match.sessionId);
      } catch (err) {
        await this.releaseGroup(group);
        throw err;
      }
    }
    return match;
  }

  private async releaseGroup(objectGroup: string): Promise<void> {
    const sessions = [undefined, ...this.targetSessions.values()];
    await Promise.allSettled(
      sessions.map(sessionId =>
        this.transport.sendCommand('Runtime.releaseObjectGroup', { objectGroup }, sessionId),
      ),
    );
  }

  private async assertObjectReady(objectId: string, sessionId?: string): Promise<void> {
    const res = await this.transport.sendCommand<{
      result?: { value?: { visible: boolean; enabled: boolean } };
    }>(
      'Runtime.callFunctionOn',
      {
        objectId,
        functionDeclaration: `function() {
          const el = this;
          if (!el || !el.isConnected) return { visible: false, enabled: false };
          const style = window.getComputedStyle(el);
          const rect = el.getBoundingClientRect();
          const checkVis = typeof el.checkVisibility === 'function'
            ? el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })
            : (style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0');
          const visible = Boolean(checkVis && (rect.width > 0 || rect.height > 0 || el.getClientRects().length > 0));
          const disabled = Boolean(el.disabled || el.getAttribute('aria-disabled') === 'true' || el.closest('fieldset[disabled]'));
          return { visible, enabled: !disabled };
        }`,
        returnByValue: true,
      },
      sessionId,
    );
    const status = res?.result?.value;
    if (!status?.visible || !status?.enabled) {
      throw new BrowserError('target_unavailable', 'Target element is not visible and enabled.');
    }
  }

  private buildTargetFinderExpression(target: Record<string, string>): string {
    const serialized = JSON.stringify(target);
    return `(() => {
      const spec = ${serialized};
      const norm = s => String(s || '').replace(/\\s+/g, ' ').trim();
      const accessibleName = el => {
        const labelledBy = el.getAttribute('aria-labelledby');
        if (labelledBy) {
          const parts = labelledBy.split(/\\s+/).map(id => document.getElementById(id)?.innerText || document.getElementById(id)?.textContent || '').filter(Boolean);
          if (parts.length) return norm(parts.join(' '));
        }
        const aria = el.getAttribute('aria-label');
        if (aria) return norm(aria);
        if (el.labels && el.labels.length) {
          return norm(Array.from(el.labels).map(l => l.innerText || l.textContent || '').join(' '));
        }
        if (el.tagName === 'INPUT' && ['button','submit','reset'].includes((el.type||'').toLowerCase())) {
          return norm(el.value || '');
        }
        if (el.tagName === 'IMG') return norm(el.alt || el.title || '');
        return norm(el.innerText || el.textContent || el.getAttribute('placeholder') || el.getAttribute('title') || '');
      };
      const implicitRole = el => {
        const explicit = el.getAttribute('role');
        if (explicit) return explicit.toLowerCase();
        const tag = el.tagName;
        const type = (el.type || '').toLowerCase();
        if (tag === 'BUTTON') return 'button';
        if (tag === 'A' && el.hasAttribute('href')) return 'link';
        if (tag === 'TEXTAREA') return 'textbox';
        if (tag === 'SELECT') return el.multiple ? 'listbox' : 'combobox';
        if (tag === 'INPUT') {
          if (type === 'checkbox') return 'checkbox';
          if (type === 'radio') return 'radio';
          if (type === 'search') return 'searchbox';
          if (['button','submit','reset'].includes(type)) return 'button';
          if (type === 'hidden') return '';
          return 'textbox';
        }
        if (/^H[1-6]$/.test(tag)) return 'heading';
        if (tag === 'IMG') return 'img';
        if (tag === 'LI') return 'listitem';
        return '';
      };
      const all = Array.from(document.querySelectorAll('*'));
      if ('test_id' in spec) {
        return all.filter(el => el.getAttribute('data-testid') === spec.test_id);
      }
      if ('label' in spec) {
        const needle = norm(spec.label);
        const ci = needle.toLowerCase();
        const controls = all.filter(el => ['INPUT','TEXTAREA','SELECT','BUTTON'].includes(el.tagName) || el.hasAttribute('role') || el.isContentEditable);
        const exact = controls.filter(el => {
          const byAria = norm(el.getAttribute('aria-label') || '');
          const byLabels = el.labels ? norm(Array.from(el.labels).map(l => l.innerText || l.textContent || '').join(' ')) : '';
          const byId = norm((el.getAttribute('aria-labelledby') || '').split(/\\s+/).map(id => document.getElementById(id)?.innerText || '').join(' '));
          const byPh = norm(el.getAttribute('placeholder') || '');
          return [byAria, byLabels, byId, byPh].some(v => v && (v === needle || v.toLowerCase() === ci));
        });
        if (exact.length > 0) return exact;
        return controls.filter(el => {
          const combined = norm([el.getAttribute('aria-label'), ...(el.labels ? Array.from(el.labels).map(l => l.textContent) : []), el.getAttribute('placeholder')].filter(Boolean).join(' ')).toLowerCase();
          return combined.includes(ci);
        });
      }
      if ('role' in spec) {
        const wantRole = spec.role.toLowerCase();
        const byRole = all.filter(el => implicitRole(el) === wantRole);
        if (!spec.name) return byRole;
        const wantName = norm(spec.name).toLowerCase();
        const exact = byRole.filter(el => accessibleName(el).toLowerCase() === wantName);
        return exact.length > 0 ? exact : byRole.filter(el => accessibleName(el).toLowerCase().includes(wantName));
      }
      if ('text' in spec) {
        const needle = norm(spec.text);
        const ci = needle.toLowerCase();
        const vis = el => {
          const st = getComputedStyle(el);
          return st.display !== 'none' && st.visibility !== 'hidden' && el.getClientRects().length > 0;
        };
        const matches = all.filter(el => {
          if (['HTML','BODY','HEAD','SCRIPT','STYLE'].includes(el.tagName) || !vis(el)) return false;
          const ownText = norm(el.innerText || el.textContent || '');
          if (ownText !== needle && ownText.toLowerCase() !== ci) return false;
          return !Array.from(el.children).some(c => {
            const ct = norm(c.innerText || c.textContent || '');
            return vis(c) && (ct === needle || ct.toLowerCase() === ci);
          });
        });
        if (matches.length > 0) return matches;
        return all.filter(el => {
          if (['HTML','BODY','HEAD','SCRIPT','STYLE'].includes(el.tagName) || !vis(el)) return false;
          const ownText = norm(el.innerText || el.textContent || '').toLowerCase();
          if (!ownText.includes(ci)) return false;
          return !Array.from(el.children).some(c => vis(c) && norm(c.innerText || c.textContent || '').toLowerCase().includes(ci));
        });
      }
      return [];
    })()`;
  }

  async inspectTargetState(resolved: CdpResolvedTarget): Promise<Record<string, unknown>> {
    const objectId = resolved.objectId ?? (await this.resolveObjectId(resolved));
    const res = await this.transport.sendCommand<{
      result?: { value?: Record<string, unknown> };
    }>(
      'Runtime.callFunctionOn',
      {
        objectId,
        functionDeclaration: `function() { return (${TARGET_STATE_SCRIPT})(this); }`,
        returnByValue: true,
      },
      resolved.sessionId,
    );
    return (res?.result?.value ?? {}) as Record<string, unknown>;
  }

  private async resolveObjectId(resolved: CdpResolvedTarget): Promise<string> {
    const res = await this.transport.sendCommand<{ object?: { objectId?: string } }>(
      'DOM.resolveNode',
      { backendNodeId: resolved.backendNodeId },
      resolved.sessionId,
    );
    const id = res?.object?.objectId;
    if (!id) {
      throw new BrowserError('target_missing', 'Requested browser target is no longer attached.');
    }
    return id;
  }

  private async elementCenter(resolved: CdpResolvedTarget): Promise<{ x: number; y: number }> {
    const objectId = resolved.objectId ?? (await this.resolveObjectId(resolved));
    await this.transport
      .sendCommand(
        'DOM.scrollIntoViewIfNeeded',
        { backendNodeId: resolved.backendNodeId },
        resolved.sessionId,
      )
      .catch(async () => {
        await this.transport.sendCommand(
          'Runtime.callFunctionOn',
          {
            objectId,
            functionDeclaration: 'function() { this.scrollIntoView({ block: "center", inline: "center" }); }',
          },
          resolved.sessionId,
        );
      });

    try {
      const box = await this.transport.sendCommand<{ model?: { content?: number[] } }>(
        'DOM.getBoxModel',
        { backendNodeId: resolved.backendNodeId },
        resolved.sessionId,
      );
      const quad = box?.model?.content;
      if (quad && quad.length >= 8) {
        const x = Math.round((quad[0] + quad[4]) / 2);
        const y = Math.round((quad[1] + quad[5]) / 2);
        return { x, y };
      }
    } catch {
      // Fallback to element rect when box model is unavailable
    }

    const rectRes = await this.transport.sendCommand<{ result?: { value?: { x: number; y: number } } }>(
      'Runtime.callFunctionOn',
      {
        objectId,
        functionDeclaration: `function() {
          const r = this.getBoundingClientRect();
          let offX = 0, offY = 0, w = window;
          while (w && w.frameElement) {
            const fr = w.frameElement.getBoundingClientRect();
            offX += fr.left; offY += fr.top;
            w = w.parent;
          }
          return { x: Math.round(offX + r.left + r.width / 2), y: Math.round(offY + r.top + r.height / 2) };
        }`,
        returnByValue: true,
      },
      resolved.sessionId,
    );
    return rectRes?.result?.value ?? { x: 0, y: 0 };
  }

  private async focusTarget(resolved: CdpResolvedTarget): Promise<void> {
    try {
      await this.transport.sendCommand(
        'DOM.focus',
        { backendNodeId: resolved.backendNodeId },
        resolved.sessionId,
      );
    } catch {
      const objectId = resolved.objectId ?? (await this.resolveObjectId(resolved));
      await this.transport.sendCommand(
        'Runtime.callFunctionOn',
        { objectId, functionDeclaration: 'function() { this.focus?.(); }' },
        resolved.sessionId,
      );
    }
  }

  private emitPointer(x: number, y: number, action: BrowserPointerAction, pressing: boolean): void {
    this.pointerSink?.({
      x: Math.round(x),
      y: Math.round(y),
      action,
      pressing,
      label: POINTER_ACTION_LABELS[action],
    });
  }

  private async pointerPause(ms: number): Promise<void> {
    if (this.pointerStepDelayMs <= 0 || ms <= 0) return;
    await new Promise(resolve => setTimeout(resolve, ms));
  }

  async movePointerSmoothly(
    x: number,
    y: number,
    options?: { sessionId?: string; action?: BrowserPointerAction },
  ): Promise<void> {
    const targetX = Math.round(x);
    const targetY = Math.round(y);
    const action = options?.action ?? 'move';
    const sessionId = options?.sessionId;
    if (!this.pointerInitialized) {
      this.pointerPos = {
        x: Math.max(24, Math.min(240, Math.round(targetX * 0.35))),
        y: Math.max(24, Math.min(180, Math.round(targetY * 0.35))),
      };
      this.pointerInitialized = true;
    }

    const points = computePointerTrajectory(this.pointerPos, { x: targetX, y: targetY });
    for (let i = 0; i < points.length; i++) {
      if (this.enablePromise === null && !this.attached && !this.transport.isAttached()) break;
      const pt = points[i];
      await this.transport.sendCommand(
        'Input.dispatchMouseEvent',
        { type: 'mouseMoved', x: pt.x, y: pt.y, button: 'none' },
        sessionId,
      );
      this.pointerPos = pt;
      this.emitPointer(pt.x, pt.y, action, false);
      if (i < points.length - 1) {
        await this.pointerPause(this.pointerStepDelayMs);
      }
    }
  }

  private async focusTargetWithPointer(resolved: CdpResolvedTarget, action: BrowserPointerAction): Promise<void> {
    try {
      const { x, y } = await this.elementCenter(resolved);
      await this.clickAt(x, y, resolved.sessionId, action);
    } catch {
      // Fallback safely to DOM focus when element layout box cannot be resolved
    }
    await this.focusTarget(resolved);
  }

  async click(resolved: CdpResolvedTarget): Promise<void> {
    const { x, y } = await this.elementCenter(resolved);
    await this.clickAt(x, y, resolved.sessionId, 'click');
  }

  async clickAt(
    x: number,
    y: number,
    sessionId?: string,
    action: BrowserPointerAction = 'click',
  ): Promise<void> {
    const targetX = Math.round(x);
    const targetY = Math.round(y);
    await this.movePointerSmoothly(targetX, targetY, { sessionId, action });
    await this.pointerPause(this.pointerStepDelayMs > 0 ? 24 : 0);
    this.emitPointer(targetX, targetY, action, true);
    await this.transport.sendCommand(
      'Input.dispatchMouseEvent',
      { type: 'mousePressed', x: targetX, y: targetY, button: 'left', buttons: 1, clickCount: 1 },
      sessionId,
    );
    await this.pointerPause(this.pointerStepDelayMs > 0 ? 45 : 0);
    await this.transport.sendCommand(
      'Input.dispatchMouseEvent',
      { type: 'mouseReleased', x: targetX, y: targetY, button: 'left', buttons: 0, clickCount: 1 },
      sessionId,
    );
    this.emitPointer(targetX, targetY, action, false);
  }

  async hover(resolved: CdpResolvedTarget): Promise<void> {
    const { x, y } = await this.elementCenter(resolved);
    await this.movePointerSmoothly(x, y, { sessionId: resolved.sessionId, action: 'hover' });
  }

  async fill(resolved: CdpResolvedTarget, value: string): Promise<void> {
    await this.focusTargetWithPointer(resolved, 'fill');
    const objectId = resolved.objectId ?? (await this.resolveObjectId(resolved));
    await this.transport.sendCommand(
      'Runtime.callFunctionOn',
      {
        objectId,
        functionDeclaration: `function() {
          this.focus?.();
          if (typeof this.select === 'function') {
            this.select();
          } else if (this.isContentEditable) {
            const range = document.createRange();
            range.selectNodeContents(this);
            const sel = window.getSelection();
            sel?.removeAllRanges();
            sel?.addRange(range);
          }
        }`,
      },
      resolved.sessionId,
    );
    if (value.length === 0) {
      await this.pressKey('Backspace', resolved.sessionId);
    } else {
      await this.insertText(value, resolved.sessionId);
    }
    // Notify controlled frameworks if input/change events did not synthesize cleanly
    await this.transport.sendCommand(
      'Runtime.callFunctionOn',
      {
        objectId,
        functionDeclaration: `function(expected) {
          if ('value' in this && String(this.value) !== expected) {
            const proto = this.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
            const desc = Object.getOwnPropertyDescriptor(proto, 'value');
            if (desc?.set) desc.set.call(this, expected);
            else this.value = expected;
            this.dispatchEvent(new Event('input', { bubbles: true }));
          }
          this.dispatchEvent(new Event('change', { bubbles: true }));
        }`,
        arguments: [{ value }],
      },
      resolved.sessionId,
    );
  }

  async type(resolved: CdpResolvedTarget, value: string): Promise<void> {
    await this.focusTargetWithPointer(resolved, 'type');
    if (value.length <= 200) {
      const charDelay = this.pointerStepDelayMs > 0 ? 4 : 0;
      for (const ch of value) {
        if (ch === '\n' || ch === '\r') {
          await this.pressKey('Enter', resolved.sessionId);
        } else {
          await this.insertText(ch, resolved.sessionId);
        }
        await this.pointerPause(charDelay);
      }
      return;
    }
    await this.insertText(value, resolved.sessionId);
  }

  async insertText(text: string, sessionId?: string): Promise<void> {
    await this.transport.sendCommand('Input.insertText', { text }, sessionId);
  }

  async press(resolved: CdpResolvedTarget, key: string): Promise<void> {
    await this.focusTarget(resolved);
    await this.pressKey(key, resolved.sessionId);
  }

  async pressKey(combo: string, sessionId?: string): Promise<void> {
    const parts = combo.split('+');
    const mainKey = parts.pop() || combo;
    let modifiers = 0;
    for (const part of parts) {
      modifiers |= MODIFIER_BITS[part] ?? 0;
    }
    const def = KEY_DEFINITIONS[mainKey];
    const key = def?.key ?? mainKey;
    const code =
      def?.code ??
      (mainKey.length === 1 && /[a-zA-Z]/.test(mainKey)
        ? `Key${mainKey.toUpperCase()}`
        : mainKey.length === 1 && /[0-9]/.test(mainKey)
          ? `Digit${mainKey}`
          : mainKey);
    const keyCode = def?.keyCode ?? (mainKey.length === 1 ? mainKey.toUpperCase().charCodeAt(0) : 0);
    const text = def?.text ?? (mainKey.length === 1 && (modifiers & ~8) === 0 ? mainKey : undefined);

    await this.transport.sendCommand(
      'Input.dispatchKeyEvent',
      {
        type: 'rawKeyDown',
        key,
        code,
        windowsVirtualKeyCode: keyCode,
        nativeVirtualKeyCode: keyCode,
        modifiers,
      },
      sessionId,
    );
    if (text) {
      await this.transport.sendCommand(
        'Input.dispatchKeyEvent',
        { type: 'char', key, code, text, unmodifiedText: text, windowsVirtualKeyCode: keyCode, modifiers },
        sessionId,
      );
    }
    await this.transport.sendCommand(
      'Input.dispatchKeyEvent',
      {
        type: 'keyUp',
        key,
        code,
        windowsVirtualKeyCode: keyCode,
        nativeVirtualKeyCode: keyCode,
        modifiers,
      },
      sessionId,
    );
  }

  async select(resolved: CdpResolvedTarget, values: string[]): Promise<void> {
    try {
      const { x, y } = await this.elementCenter(resolved);
      await this.movePointerSmoothly(x, y, { sessionId: resolved.sessionId, action: 'select' });
    } catch {
      // Proceed even if layout box is unavailable
    }
    const objectId = resolved.objectId ?? (await this.resolveObjectId(resolved));
    const res = await this.transport.sendCommand<{ result?: { value?: boolean } }>(
      'Runtime.callFunctionOn',
      {
        objectId,
        functionDeclaration: `function(wanted) {
          if (this.tagName !== 'SELECT') return false;
          const set = new Set(wanted);
          let matched = 0;
          for (const opt of Array.from(this.options || [])) {
            const hit = set.has(opt.value) || set.has((opt.text || '').trim());
            opt.selected = this.multiple ? hit : (!matched && hit);
            if (opt.selected) matched++;
          }
          if (matched === 0) return false;
          this.dispatchEvent(new Event('input', { bubbles: true }));
          this.dispatchEvent(new Event('change', { bubbles: true }));
          return true;
        }`,
        arguments: [{ value: values }],
        returnByValue: true,
      },
      resolved.sessionId,
    );
    if (!res?.result?.value) {
      throw new BrowserError('target_unavailable', 'Requested option values could not be selected.');
    }
  }

  async check(resolved: CdpResolvedTarget, checked: boolean): Promise<void> {
    const objectId = resolved.objectId ?? (await this.resolveObjectId(resolved));
    const stateRes = await this.transport.sendCommand<{ result?: { value?: boolean } }>(
      'Runtime.callFunctionOn',
      { objectId, functionDeclaration: 'function() { return Boolean(this.checked); }', returnByValue: true },
      resolved.sessionId,
    );
    if (Boolean(stateRes?.result?.value) === checked) {
      try {
        const { x, y } = await this.elementCenter(resolved);
        await this.movePointerSmoothly(x, y, { sessionId: resolved.sessionId, action: 'check' });
      } catch {
        // Ignore missing layout box
      }
      return;
    }
    const { x, y } = await this.elementCenter(resolved);
    await this.clickAt(x, y, resolved.sessionId, 'check');
    const verifyRes = await this.transport.sendCommand<{ result?: { value?: boolean } }>(
      'Runtime.callFunctionOn',
      { objectId, functionDeclaration: 'function() { return Boolean(this.checked); }', returnByValue: true },
      resolved.sessionId,
    );
    if (Boolean(verifyRes?.result?.value) !== checked) {
      await this.transport.sendCommand(
        'Runtime.callFunctionOn',
        {
          objectId,
          functionDeclaration: `function(v) {
            this.checked = v;
            this.dispatchEvent(new Event('input', { bubbles: true }));
            this.dispatchEvent(new Event('change', { bubbles: true }));
          }`,
          arguments: [{ value: checked }],
        },
        resolved.sessionId,
      );
    }
  }

  async scroll(deltaX: number, deltaY: number, viewport: { width: number; height: number }): Promise<void> {
    await this.ensureAttached();
    const sx =
      this.pointerInitialized && this.pointerPos.x > 16 && this.pointerPos.x < viewport.width - 16
        ? this.pointerPos.x
        : Math.max(1, Math.floor(viewport.width / 2));
    const sy =
      this.pointerInitialized && this.pointerPos.y > 16 && this.pointerPos.y < viewport.height - 16
        ? this.pointerPos.y
        : Math.max(1, Math.floor(viewport.height / 2));
    await this.movePointerSmoothly(sx, sy, { action: 'scroll' });
    this.emitPointer(sx, sy, 'scroll', false);
    await this.transport.sendCommand('Input.dispatchMouseEvent', {
      type: 'mouseWheel',
      x: sx,
      y: sy,
      deltaX,
      deltaY,
    });
  }

  async waitForTarget(
    target: Record<string, string>,
    state: string,
    timeoutMs: number,
  ): Promise<void> {
    const deadline = performance.now() + timeoutMs;
    while (performance.now() <= deadline) {
      try {
        if (state === 'detached') {
          try {
            await this.resolveTarget(target, false);
          } catch (err) {
            if (err instanceof BrowserError && err.code === 'target_missing') return;
            throw err;
          }
        } else if (state === 'hidden') {
          try {
            await this.resolveTarget(target, true);
          } catch (err) {
            if (
              err instanceof BrowserError &&
              (err.code === 'target_missing' || err.code === 'target_unavailable')
            ) {
              return;
            }
            throw err;
          }
        } else if (state === 'attached') {
          await this.resolveTarget(target, false);
          return;
        } else {
          await this.resolveTarget(target, true);
          return;
        }
      } catch (err) {
        if (
          !(err instanceof BrowserError) ||
          !['target_missing', 'target_unavailable', 'ambiguous_target'].includes(err.code)
        ) {
          throw err;
        }
      }
      await new Promise(resolve => setTimeout(resolve, 60));
    }
    throw new BrowserError('browser_operation_failed', 'Timed out waiting for target state.');
  }

  async extractText(maxChars: number): Promise<{ content: string; truncated: boolean }> {
    const frames = await this.listFrames();
    const mainFrame = frames.find(f => !f.parentId) ?? frames[0];
    if (!mainFrame) return { content: '', truncated: false };
    const raw = await this.evaluateInFrame<string>(
      mainFrame,
      'String(document.body?.innerText || "")',
    );
    const cleaned = redact(String(raw ?? ''), maxChars + 1);
    return {
      content: cleaned.slice(0, maxChars),
      truncated: cleaned.length > maxChars,
    };
  }

  async getLinks(): Promise<{ total: number; links: { text: string; url: string }[] }> {
    const frames = await this.listFrames();
    const mainFrame = frames.find(f => !f.parentId) ?? frames[0];
    if (!mainFrame) return { total: 0, links: [] };
    return this.evaluateInFrame<{ total: number; links: { text: string; url: string }[] }>(
      mainFrame,
      `(() => {
        const anchors = Array.from(document.querySelectorAll('a[href]'));
        return {
          total: anchors.length,
          links: anchors.slice(0, 200).map(e => ({
            text: String(e.innerText || e.getAttribute('aria-label') || '').slice(0, 300),
            url: String(e.href || '').slice(0, 8192)
          }))
        };
      })()`,
    );
  }

  async getForms(): Promise<{
    total: number;
    forms: {
      method: string;
      action: string;
      fieldCount: number;
      fields: { name: string; label: string; tag: string; type: string; required: boolean; disabled: boolean }[];
    }[];
  }> {
    const frames = await this.listFrames();
    const mainFrame = frames.find(f => !f.parentId) ?? frames[0];
    if (!mainFrame) return { total: 0, forms: [] };
    return this.evaluateInFrame(
      mainFrame,
      `(() => {
        const forms = Array.from(document.forms || []);
        return {
          total: forms.length,
          forms: forms.slice(0, 50).map(f => ({
            method: f.method,
            action: String(f.action || '').slice(0, 8192),
            fieldCount: f.elements.length,
            fields: Array.from(f.elements).slice(0, 100).map(e => ({
              tag: e.tagName,
              type: e.type || '',
              name: String(e.name || '').slice(0, 256),
              label: String(e.getAttribute('aria-label') || '').slice(0, 512),
              required: !!e.required,
              disabled: !!e.disabled
            }))
          }))
        };
      })()`,
    );
  }

  /**
   * Captures a PNG screenshot after masking input controls across ALL frames:
   * - `host === false` (model view): masks every input, textarea, select, and
   *   contenteditable box identically with neutral fills so distinct values never
   *   alter rendered pixels or length leaks.
   * - `host === true` (human preview): masks password/sensitive credentials only.
   */
  async captureMaskedScreenshot(options: {
    fullPage?: boolean;
    host?: boolean;
    maxBytes: number;
  }): Promise<Uint8Array> {
    const frames = await this.listFrames();
    const maskId = '__firik_mask_style__';
    const maskSelector = options.host
      ? 'input[type="password" i], input[autocomplete*="password" i], input[autocomplete*="cc-" i], input[autocomplete="one-time-code" i]'
      : 'input, textarea, select, [contenteditable]';

    const injectExpr = `(() => {
      if (document.getElementById(${JSON.stringify(maskId)})) return;
      const st = document.createElement('style');
      st.id = ${JSON.stringify(maskId)};
      st.textContent = ${JSON.stringify(
        `${maskSelector}{color:transparent!important;text-shadow:none!important;caret-color:transparent!important;background:#e2e8f0!important;-webkit-text-fill-color:transparent!important;overflow:hidden!important;} ${maskSelector}::placeholder{color:transparent!important;} ${maskSelector}::-webkit-scrollbar{display:none!important;}`,
      )};
      (document.head || document.documentElement).appendChild(st);
    })()`;

    const cleanupExpr = `(() => { document.getElementById(${JSON.stringify(maskId)})?.remove(); })()`;

    for (const frame of frames) {
      await this.evaluateInFrame(frame, injectExpr).catch(() => {});
    }
    try {
      const params: Record<string, unknown> = { format: 'png' };
      if (options.fullPage) {
        const metrics = await this.transport
          .sendCommand<{ cssContentSize?: { width: number; height: number } }>('Page.getLayoutMetrics')
          .catch(() => null);
        if (metrics?.cssContentSize?.width && metrics?.cssContentSize?.height) {
          params.captureBeyondViewport = true;
          params.clip = {
            x: 0,
            y: 0,
            width: metrics.cssContentSize.width,
            height: metrics.cssContentSize.height,
            scale: 1,
          };
        }
      }
      const res = await this.transport.sendCommand<{ data: string }>('Page.captureScreenshot', params);
      const bytes = Buffer.from(res.data, 'base64');
      if (bytes.byteLength > options.maxBytes) {
        throw new BrowserError('browser_limit', 'Browser screenshot exceeds size limit.');
      }
      return new Uint8Array(bytes);
    } finally {
      for (const frame of frames) {
        await this.evaluateInFrame(frame, cleanupExpr).catch(() => {});
      }
    }
  }
}
