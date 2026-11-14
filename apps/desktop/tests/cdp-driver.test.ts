import { EventEmitter } from 'node:events';
import { describe, expect, it } from 'vitest';
import { CdpPageDriver, type CdpTransport } from '../src/main/cdp-driver.js';
import { BrowserError } from '../src/browser-worker/models.js';

interface AxNodeStub {
  nodeId: string;
  role?: { value?: string };
  name?: { value?: string };
  value?: { value?: string };
  properties?: { name: string; value?: { value?: unknown } }[];
  childIds?: string[];
  backendDOMNodeId?: number;
  ignored?: boolean;
}

class MockCdpTransport extends EventEmitter implements CdpTransport {
  private attached = false;
  public calls: { method: string; params?: Record<string, unknown>; sessionId?: string }[] = [];
  public failScreenshot = false;
  public mainFrameUrl = 'https://example.test/app';
  public childFrameUrl = 'https://sub.example.test/frame';
  public oopifUrl = 'https://checkout.example.test/pay';

  public mainAxNodes: AxNodeStub[] = [
    { nodeId: 'ax-root', role: { value: 'RootWebArea' }, name: { value: 'Example App' }, childIds: ['ax-h1', 'ax-input', 'ax-btn', 'ax-iframe'] },
    { nodeId: 'ax-h1', role: { value: 'heading' }, name: { value: 'Account Overview' }, properties: [{ name: 'level', value: { value: 1 } }], backendDOMNodeId: 10 },
    {
      nodeId: 'ax-input',
      role: { value: 'textbox' },
      name: { value: 'Email Address' },
      value: { value: 'secret-email@example.test' },
      childIds: ['ax-input-text'],
      backendDOMNodeId: 11,
    },
    { nodeId: 'ax-input-text', role: { value: 'StaticText' }, name: { value: 'secret-email@example.test' } },
    { nodeId: 'ax-btn', role: { value: 'button' }, name: { value: 'Continue' }, backendDOMNodeId: 12 },
    { nodeId: 'ax-iframe', role: { value: 'Iframe' }, name: { value: '' }, backendDOMNodeId: 13 },
  ];

  public oopifAxNodes: AxNodeStub[] = [
    { nodeId: 'op-root', role: { value: 'RootWebArea' }, name: { value: 'Checkout' }, childIds: ['op-input', 'op-btn'] },
    { nodeId: 'op-input', role: { value: 'textbox' }, name: { value: 'One-Time Code' }, value: { value: '998877' }, childIds: ['op-input-text'], backendDOMNodeId: 21 },
    { nodeId: 'op-input-text', role: { value: 'StaticText' }, name: { value: '998877' } },
    { nodeId: 'op-btn', role: { value: 'button' }, name: { value: 'Confirm Payment' }, backendDOMNodeId: 22 },
  ];

  private objCounter = 1;
  public nodeObjectMap = new Map<string, number>();

  isAttached(): boolean {
    return this.attached;
  }

  attach(_version?: string): void {
    this.attached = true;
  }

  detach(): void {
    this.attached = false;
    this.emit('detach', {}, 'target_closed');
  }

  emitTargetAttached(sessionId: string, frameId: string, url: string): void {
    this.emit('message', {}, 'Target.attachedToTarget', {
      sessionId,
      targetInfo: { targetId: `target-${frameId}`, type: 'iframe', url },
    });
  }

  async sendCommand<T = unknown>(method: string, params: Record<string, unknown> = {}, sessionId?: string): Promise<T> {
    this.calls.push({ method, params, sessionId });

    switch (method) {
      case 'Page.enable':
      case 'DOM.enable':
      case 'Runtime.enable':
      case 'Accessibility.enable':
      case 'Target.setAutoAttach':
      case 'Emulation.setTimezoneOverride':
      case 'Emulation.setLocaleOverride':
      case 'Emulation.setUserAgentOverride':
      case 'Runtime.releaseObjectGroup':
      case 'DOM.scrollIntoViewIfNeeded':
      case 'DOM.focus':
      case 'Input.dispatchMouseEvent':
      case 'Input.insertText':
      case 'Input.dispatchKeyEvent':
        return {} as T;

      case 'Page.getFrameTree':
        if (sessionId === 'oopif-1') {
          return {
            frameTree: {
              frame: { id: 'f-oopif', url: this.oopifUrl, name: 'checkout-frame' },
              childFrames: [],
            },
          } as T;
        }
        return {
          frameTree: {
            frame: { id: 'f-main', url: this.mainFrameUrl, name: 'main' },
            childFrames: [{ frame: { id: 'f-sub', parentId: 'f-main', url: this.childFrameUrl, name: 'sub-frame' }, childFrames: [] }],
          },
        } as T;

      case 'Page.createIsolatedWorld': {
        const fid = String(params.frameId ?? '');
        const ctx = sessionId === 'oopif-1' ? 200 : fid === 'f-main' ? 100 : 101;
        return { executionContextId: ctx } as T;
      }

      case 'Accessibility.getFullAXTree':
        if (sessionId === 'oopif-1') {
          return { nodes: this.oopifAxNodes } as T;
        }
        if (params.frameId === 'f-sub') {
          return { nodes: [{ nodeId: 'sub-root', role: { value: 'RootWebArea' }, name: { value: 'Sub' }, childIds: [] }] } as T;
        }
        return { nodes: this.mainAxNodes } as T;

      case 'DOM.describeNode': {
        if (params.backendNodeId === 13) {
          return { node: { contentDocument: { frameId: 'f-oopif' } } } as T;
        }
        const objId = String(params.objectId ?? '');
        const backendNodeId = this.nodeObjectMap.get(objId) ?? 12;
        return { node: { backendNodeId, nodeName: 'BUTTON', attributes: [] } } as T;
      }

      case 'DOM.resolveNode': {
        const backendNodeId = Number(params.backendNodeId ?? 0);
        const objectId = `obj-${backendNodeId}-${this.objCounter++}`;
        this.nodeObjectMap.set(objectId, backendNodeId);
        return { object: { objectId } } as T;
      }

      case 'DOM.getBoxModel':
        return {
          model: {
            content: [100, 200, 160, 200, 160, 230, 100, 230],
          },
        } as T;

      case 'Runtime.evaluate': {
        const expr = String(params.expression ?? '');
        if (expr.includes('const spec =')) {
          if (params.contextId !== 100) {
            return { result: { objectId: 'empty-list' } } as T;
          }
          // Semantic locator query inside main frame isolated world
          const argsMatch = expr.includes('"label":"Email Address"') || expr.includes('"test_id":"submit-btn"') || expr.includes('"text":"Continue"');
          const ambiguous = expr.includes('"text":"DupButton"');
          if (argsMatch) {
            const objectId = `match-list-${this.objCounter++}`;
            return { result: { objectId } } as T;
          }
          if (ambiguous) {
            return { result: { objectId: `ambig-list-${this.objCounter++}` } } as T;
          }
          return { result: { objectId: 'empty-list' } } as T;
        }
        if (expr.includes('document.querySelectorAll(\'a[href]\')')) {
          return { result: { value: [{ text: 'Docs', url: 'https://example.test/docs' }] } } as T;
        }
        if (expr.includes('document.querySelectorAll')) {
          // Digest evaluation
          return { result: { value: 'Example App|inputs:1|hashes:12' } } as T;
        }
        if (expr.includes('__firik_mask_style__')) {
          return { result: { value: true } } as T;
        }
        if (expr.includes('document.body.innerText')) {
          return { result: { value: 'Account Overview Continue' } } as T;
        }
        return { result: { value: { ok: true } } } as T;
      }

      case 'Runtime.getProperties': {
        const oid = String(params.objectId ?? '');
        if (oid.startsWith('match-list-')) {
          const itemObj = `elem-${this.objCounter++}`;
          this.nodeObjectMap.set(itemObj, 12);
          return {
            result: [{ enumerable: true, name: '0', value: { type: 'object', objectId: itemObj } }],
          } as T;
        }
        if (oid.startsWith('ambig-list-')) {
          const o1 = `elem-${this.objCounter++}`;
          const o2 = `elem-${this.objCounter++}`;
          this.nodeObjectMap.set(o1, 12);
          this.nodeObjectMap.set(o2, 12);
          return {
            result: [
              { enumerable: true, name: '0', value: { type: 'object', objectId: o1 } },
              { enumerable: true, name: '1', value: { type: 'object', objectId: o2 } },
            ],
          } as T;
        }
        return { result: [] } as T;
      }

      case 'Runtime.callFunctionOn': {
        const fnDecl = String(params.functionDeclaration ?? '');
        if (fnDecl.includes('formFieldCount')) {
          return {
            result: {
              value: {
                tag: 'button',
                role: 'button',
                type: '',
                name: 'Continue',
                label: 'Continue',
                html: '<button>Continue</button>',
                value: '',
                valueLength: 0,
                formFieldCount: 0,
                fields: [],
                sensitive: false,
                visible: true,
                disabled: false,
              },
            },
          } as T;
        }
        if (fnDecl.includes('getBoundingClientRect') && fnDecl.includes('visible')) {
          return { result: { value: { ready: true, visible: true, enabled: true } } } as T;
        }
        if (fnDecl.includes('disabled')) {
          return { result: { value: { ready: true, visible: true, enabled: true } } } as T;
        }
        return { result: { value: { ok: true, selected: ['opt1'], checked: true } } } as T;
      }

      case 'Page.getLayoutMetrics':
        return { cssContentSize: { width: 1024, height: 1800 } } as T;

      case 'Page.captureScreenshot':
        if (this.failScreenshot) {
          throw new Error('screenshot failed');
        }
        return { data: Buffer.from('FAKE_PNG').toString('base64') } as T;

      default:
        return {} as T;
    }
  }
}

describe('CdpPageDriver', () => {
  it('redacts textbox values and child StaticText in ariaSnapshot and traverses OOPiFs', async () => {
    const transport = new MockCdpTransport();
    const driver = new CdpPageDriver(transport);
    await driver.ensureAttached();
    transport.emitTargetAttached('oopif-1', 'f-oopif', transport.oopifUrl);

    const snapshot = await driver.ariaSnapshot(4000);
    expect(snapshot.content).toContain('heading "Account Overview"');
    expect(snapshot.content).toContain('button "Continue"');
    expect(snapshot.content).toContain('[value omitted]');
    expect(snapshot.content).not.toContain('secret-email@example.test');
    expect(snapshot.content).not.toContain('998877');
    expect(snapshot.content).toContain('button "Confirm Payment"');
    expect(snapshot.frames.map(f => f.url)).toEqual([
      transport.childFrameUrl,
      transport.oopifUrl,
    ]);

    // Ref resolution points straight back to OOPiF target with its sessionId
    const oopifMatch = /button "Confirm Payment" \[ref=(e\d+)\]/.exec(snapshot.content);
    expect(oopifMatch).not.toBeNull();
    const resolved = await driver.resolveTarget({ ref: oopifMatch![1] });
    expect(resolved.sessionId).toBe('oopif-1');
    expect(resolved.backendNodeId).toBe(22);
  });

  it('resolves semantic targets, enforces uniqueness, and inspects target state', async () => {
    const transport = new MockCdpTransport();
    const driver = new CdpPageDriver(transport);
    await driver.ariaSnapshot(4000);

    const byLabel = await driver.resolveTarget({ label: 'Email Address' });
    expect(byLabel.backendNodeId).toBe(12);

    const byTestId = await driver.resolveTarget({ test_id: 'submit-btn' });
    expect(byTestId.backendNodeId).toBe(12);

    const byText = await driver.resolveTarget({ text: 'Continue' });
    expect(byText.backendNodeId).toBe(12);

    await expect(driver.resolveTarget({ text: 'DupButton' })).rejects.toThrow(BrowserError);
    await expect(driver.resolveTarget({ label: 'Does Not Exist' })).rejects.toThrow(BrowserError);

    const state = await driver.inspectTargetState(byLabel);
    expect(state.tag).toBe('button');
    expect(state.sensitive).toBe(false);
  });

  it('executes CDP gestures and captures/cleans up screenshot masks even on failure', async () => {
    const transport = new MockCdpTransport();
    const consoleEvents: Record<string, unknown>[] = [];
    const pageErrors: Record<string, unknown>[] = [];
    const driver = new CdpPageDriver(transport, {
      onConsole: entry => consoleEvents.push(entry as unknown as Record<string, unknown>),
      onPageError: entry => pageErrors.push(entry as unknown as Record<string, unknown>),
    });
    const snapshot = await driver.ariaSnapshot(4000);
    const btnRef = /button "Continue" \[ref=(e\d+)\]/.exec(snapshot.content)![1];
    const resolved = await driver.resolveTarget({ ref: btnRef });

    await driver.click(resolved);
    await driver.fill(resolved, 'hello');
    await driver.pressKey('Enter');
    await driver.scroll(0, 120, { width: 800, height: 600 });

    const methods = transport.calls.map(c => c.method);
    expect(methods).toContain('Input.dispatchMouseEvent');
    expect(methods).toContain('Input.insertText');
    expect(methods).toContain('Input.dispatchKeyEvent');

    transport.emit('message', {}, 'Runtime.consoleAPICalled', { type: 'error', args: [{ value: 'hi' }] });
    transport.emit('message', {}, 'Runtime.exceptionThrown', { exceptionDetails: { text: 'boom' } });
    expect(consoleEvents.length).toBe(1);
    expect(pageErrors.length).toBe(1);

    const bytes = await driver.captureMaskedScreenshot({ host: false, fullPage: true, maxBytes: 1024 * 1024 });
    expect(Buffer.from(bytes).toString('utf8')).toBe('FAKE_PNG');

    // Cleanup must still execute if Page.captureScreenshot throws
    transport.calls = [];
    transport.failScreenshot = true;
    await expect(driver.captureMaskedScreenshot({ host: false, maxBytes: 1024 * 1024 })).rejects.toThrow();
    const evalsAfterFail = transport.calls
      .filter(c => c.method === 'Runtime.evaluate')
      .map(c => String(c.params?.expression ?? ''));
    expect(evalsAfterFail.some(expr => expr.includes('.remove()'))).toBe(true);
  });

  it('emits smooth curved pointer steps, pressing transitions, and acceptLanguage override', async () => {
    const transport = new MockCdpTransport();
    const pointerEvents: Array<{ action: string; pressing: boolean; x: number; y: number }> = [];
    const driver = new CdpPageDriver(transport, {
      pointerStepDelayMs: 0,
      onPointer: event => pointerEvents.push(event),
    });

    await driver.setEmulation({
      locale: 'tr-TR',
      acceptLanguage: 'tr-TR,tr;q=0.9,en-US;q=0.8',
      userAgent: 'TestBrowser/1.0',
    });
    const uaCall = transport.calls.find(c => c.method === 'Emulation.setUserAgentOverride');
    expect(uaCall?.params).toMatchObject({
      userAgent: 'TestBrowser/1.0',
      acceptLanguage: 'tr-TR,tr;q=0.9,en-US;q=0.8',
    });

    const snapshot = await driver.ariaSnapshot(4000);
    const btnRef = /button "Continue" \[ref=(e\d+)\]/.exec(snapshot.content)![1];
    const resolved = await driver.resolveTarget({ ref: btnRef });

    transport.calls = [];
    await driver.hover(resolved);
    const hoverMoves = transport.calls.filter(
      c => c.method === 'Input.dispatchMouseEvent' && c.params?.type === 'mouseMoved',
    );
    expect(hoverMoves.length).toBeGreaterThanOrEqual(3);
    expect(pointerEvents.some(ev => ev.action === 'hover' && !ev.pressing)).toBe(true);

    transport.calls = [];
    pointerEvents.length = 0;
    await driver.click(resolved);
    const mouseEvents = transport.calls
      .filter(c => c.method === 'Input.dispatchMouseEvent')
      .map(c => c.params?.type);
    const pressedIdx = mouseEvents.indexOf('mousePressed');
    const releasedIdx = mouseEvents.indexOf('mouseReleased');
    expect(pressedIdx).toBeGreaterThanOrEqual(1);
    expect(releasedIdx).toBeGreaterThan(pressedIdx);
    const pressingIndex = pointerEvents.findIndex(ev => ev.action === 'click' && ev.pressing);
    const releasedPointerIndex = pointerEvents.findIndex(
      (ev, idx) => idx > pressingIndex && ev.action === 'click' && !ev.pressing,
    );
    expect(pressingIndex).toBeGreaterThanOrEqual(0);
    expect(releasedPointerIndex).toBeGreaterThan(pressingIndex);
  });
});
