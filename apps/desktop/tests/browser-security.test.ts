import { describe, expect, it } from 'vitest';
import {
  BrowserSecurityPolicy,
  isGlobalIp,
  originOf,
  redact,
  sanitizedUrl,
  sanitizeSecrets,
  validateGrant,
  validateIdentity,
} from '../src/browser-worker/index.js';
import { normalizeAddressEntry } from '../src/renderer/browser-panel.js';

describe('Browser security and URL policy', () => {
  it('canonicalizes origins and rejects non-HTTP schemes or embedded credentials', () => {
    expect(originOf('https://Example.COM:443/path?secret=1#frag')).toBe('https://example.com');
    expect(originOf('http://127.0.0.1:8080/test')).toBe('http://127.0.0.1:8080');
    expect(originOf('http://[::1]:3000/page')).toBe('http://[::1]:3000');

    expect(() => originOf('javascript:alert(1)')).toThrow();
    expect(() => originOf('file:///etc/passwd')).toThrow();
    expect(() => originOf('data:text/html,hello')).toThrow();
    expect(() => originOf('https://user:pass@example.com/')).toThrow();
  });

  it('classifies global vs private/reserved IPs accurately across IPv4 and IPv6', () => {
    expect(isGlobalIp('93.184.216.34')).toBe(true);
    expect(isGlobalIp('1.1.1.1')).toBe(true);
    expect(isGlobalIp('2606:2800:220:1:248:1893:25c8:1946')).toBe(true);

    expect(isGlobalIp('127.0.0.1')).toBe(false);
    expect(isGlobalIp('10.0.0.1')).toBe(false);
    expect(isGlobalIp('172.16.5.4')).toBe(false);
    expect(isGlobalIp('192.168.1.10')).toBe(false);
    expect(isGlobalIp('169.254.169.254')).toBe(false);
    expect(isGlobalIp('100.64.0.1')).toBe(false);
    expect(isGlobalIp('::1')).toBe(false);
    expect(isGlobalIp('fe80::1')).toBe(false);
    expect(isGlobalIp('fc00::1')).toBe(false);
    expect(isGlobalIp('::ffff:127.0.0.1')).toBe(false);
  });

  it('enforces public HTTPS, origin allowlists, and DNS rebinding checks', async () => {
    const publicIps = new Map<string, string[]>([
      ['example.com', ['93.184.216.34']],
      ['rebind.example.com', ['127.0.0.1']],
      ['metadata.example.com', ['169.254.169.254']],
    ]);
    const policy = new BrowserSecurityPolicy(async host => publicIps.get(host) ?? ['93.184.216.34']);

    const publicGrant = validateGrant(
      {
        enabled: true,
        allow_public_https: true,
        origins: [],
        allow_private_origins: [],
      },
      originOf,
    );

    await expect(policy.validate('https://example.com/docs', publicGrant)).resolves.toBe('https://example.com');
    await expect(policy.validate('http://example.com/docs', publicGrant)).rejects.toMatchObject({
      code: 'insecure_http_denied',
    });
    await expect(policy.validate('https://rebind.example.com/', publicGrant)).rejects.toMatchObject({
      code: 'private_network_denied',
    });
    await expect(policy.validate('https://metadata.example.com/', publicGrant)).rejects.toMatchObject({
      code: 'private_network_denied',
    });
    await expect(policy.validate('https://127.0.0.1:8443/', publicGrant)).rejects.toMatchObject({
      code: 'private_network_denied',
    });

    const localGrant = validateGrant(
      {
        enabled: true,
        allow_public_https: false,
        origins: ['http://127.0.0.1:8080'],
        allow_private_origins: ['http://127.0.0.1:8080'],
      },
      originOf,
    );
    await expect(policy.validate('http://127.0.0.1:8080/app', localGrant)).resolves.toBe('http://127.0.0.1:8080');
    await expect(policy.validate('https://example.com/', localGrant)).rejects.toMatchObject({
      code: 'origin_denied',
    });

    const disabled = validateGrant({ enabled: false }, originOf);
    await expect(policy.validate('https://example.com/', disabled)).rejects.toMatchObject({
      code: 'permission_denied',
    });
  });

  it('redacts sensitive tokens, query strings, and secret headers', () => {
    expect(sanitizedUrl('https://example.com/reset?token=secret123#hash')).toBe('https://example.com/reset');
    expect(sanitizedUrl('javascript:alert(1)')).toBe('[blocked URL]');
    const sample = 'Authorization: Bearer sk-1234567890abcdef1234567890abcdef and api_key=my_secret_value';
    const cleaned = redact(sample);
    expect(cleaned).not.toContain('sk-1234567890abcdef1234567890abcdef');
    expect(cleaned).not.toContain('my_secret_value');
    expect(redact('see https://example.com/callback?code=abc123456 now')).toBe(
      'see https://example.com/callback now',
    );
    expect(sanitizeSecrets('token=supersecretvalue123')).not.toContain('supersecretvalue123');
  });

  it('validates identities, grants, and address bar normalization', () => {
    const id = validateIdentity({ conversation_id: 'conv-1', actor_id: 'parent' });
    expect(id.conversation_id).toBe('conv-1');
    expect(() => validateIdentity({ conversation_id: '', actor_id: 'parent' })).toThrow();
    expect(() =>
      validateGrant(
        {
          enabled: true,
          origins: ['https://example.com'],
          allow_private_origins: ['http://127.0.0.1:3000'],
        },
        originOf,
      ),
    ).toThrow(/subset/);

    expect(normalizeAddressEntry('example.com/test')).toBe('https://example.com/test');
    expect(normalizeAddressEntry('http://127.0.0.1:3000/app')).toBe('http://127.0.0.1:3000/app');
    expect(normalizeAddressEntry('localhost:5173/ui')).toBe('http://localhost:5173/ui');
    expect(normalizeAddressEntry('firik agent nedir')).toBe(
      'https://www.google.com/search?q=firik%20agent%20nedir',
    );
    expect(() => normalizeAddressEntry('javascript:alert(1)')).toThrow();
    expect(() => normalizeAddressEntry('file:///etc/passwd')).toThrow();
    expect(() => normalizeAddressEntry('data:text/html,hi')).toThrow();
  });
});
