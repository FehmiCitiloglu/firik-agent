import { lookup } from 'node:dns/promises';
import { isIP } from 'node:net';
import { BrowserError, type BrowserGrant } from './models.js';

export function originOf(url: string): string {
  try {
    if (
      typeof url !== 'string' ||
      url.length === 0 ||
      url.length > 8192 ||
      [...url].some(ch => ch.charCodeAt(0) <= 32) ||
      url.includes('\\')
    ) {
      throw new Error('invalid');
    }
    const match = /^([a-zA-Z][a-zA-Z0-9+.-]*):\/\/([^/?#]*)/.exec(url);
    if (!match) {
      throw new Error('invalid');
    }
    const scheme = match[1].toLowerCase();
    if (scheme !== 'http' && scheme !== 'https') {
      throw new Error('invalid');
    }
    const rawAuthority = match[2];
    if (!rawAuthority || rawAuthority.includes('@') || rawAuthority.includes('%')) {
      throw new Error('invalid');
    }
    let explicitPort: number | null = null;
    if (rawAuthority.startsWith('[')) {
      const closeIdx = rawAuthority.indexOf(']');
      if (closeIdx < 0) {
        throw new Error('invalid');
      }
      const rest = rawAuthority.slice(closeIdx + 1);
      if (rest !== '') {
        if (!/^:\d+$/.test(rest)) {
          throw new Error('invalid');
        }
        explicitPort = Number(rest.slice(1));
      }
    } else {
      const parts = rawAuthority.split(':');
      if (parts.length > 2) {
        throw new Error('invalid');
      }
      if (parts.length === 2) {
        if (!/^\d+$/.test(parts[1])) {
          throw new Error('invalid');
        }
        explicitPort = Number(parts[1]);
      }
    }
    if (explicitPort !== null && (!Number.isInteger(explicitPort) || explicitPort < 1 || explicitPort > 65535)) {
      throw new Error('invalid');
    }
    const parsed = new URL(url);
    if (parsed.username || parsed.password) {
      throw new Error('invalid');
    }
    let host = parsed.hostname.toLowerCase();
    if (!host.startsWith('[')) {
      host = host.replace(/\.$/, '');
    }
    if (!host || host.includes('%')) {
      throw new Error('invalid');
    }
    const defaultPort = scheme === 'https' ? 443 : 80;
    const port = explicitPort ?? (parsed.port ? Number(parsed.port) : defaultPort);
    if (!Number.isInteger(port) || port < 1 || port > 65535) {
      throw new Error('invalid');
    }
    const suffix = port === defaultPort ? '' : `:${port}`;
    return `${scheme}://${host}${suffix}`;
  } catch {
    throw new BrowserError('invalid_url', 'Only HTTP(S) URLs without credentials are permitted.');
  }
}

function stripUrlQuery(rawUrl: string): string {
  try {
    const parsed = new URL(rawUrl);
    const hostPort = parsed.host;
    return `${parsed.protocol}//${hostPort}${parsed.pathname}`;
  } catch {
    return '[URL]';
  }
}

export function sanitizeSecrets(input: string): string {
  let text = String(input);
  text = text.replace(/\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{6,}/gi, '$1 [REDACTED]');
  text = text.replace(
    /\b(password|passwd|secret|token|api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|authorization|security[_-]?code|otp)\s*([:=])\s*([^\s"',;}{]+)/gi,
    '$1$2[REDACTED]',
  );
  text = text.replace(
    /\b(?:sk-[A-Za-z0-9_-]{12,}|firik_llm_[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{16,}|AIza[0-9A-Za-z-_]{20,})\b/g,
    '[REDACTED]',
  );
  return text;
}

export function redact(value: unknown, limit = 2000): string {
  let text = sanitizeSecrets(String(value ?? ''));
  text = text.replace(/https?:\/\/[^\s<>"']+/g, match => stripUrlQuery(match));
  return text.slice(0, limit);
}

export function sanitizedUrl(url: string): string {
  try {
    const origin = originOf(url);
    const parsed = new URL(url);
    return redact(origin + parsed.pathname, 2000);
  } catch {
    return url === 'about:blank' ? url : '[blocked URL]';
  }
}

function parseIpv4Int(ip: string): number | null {
  const parts = ip.split('.');
  if (parts.length !== 4) return null;
  let value = 0;
  for (const part of parts) {
    if (!/^\d+$/.test(part)) return null;
    const num = Number(part);
    if (num < 0 || num > 255) return null;
    value = ((value << 8) | num) >>> 0;
  }
  return value;
}

function isGlobalIpv4(ip: string): boolean {
  const addr = parseIpv4Int(ip);
  if (addr === null) return false;
  const inCidr = (base: number, prefix: number): boolean => {
    const mask = prefix === 0 ? 0 : (~0 << (32 - prefix)) >>> 0;
    return (addr & mask) >>> 0 === (base & mask) >>> 0;
  };
  // 192.0.0.9/32 and 192.0.0.10/32 are globally reachable per IANA / Python ipaddress
  if (addr === 0xc0000009 || addr === 0xc000000a) return true;
  if (
    inCidr(0x00000000, 8) || // 0.0.0.0/8
    inCidr(0x0a000000, 8) || // 10.0.0.0/8
    inCidr(0x64400000, 10) || // 100.64.0.0/10 (Shared CGNAT)
    inCidr(0x7f000000, 8) || // 127.0.0.0/8 (Loopback)
    inCidr(0xa9fe0000, 16) || // 169.254.0.0/16 (Link-local)
    inCidr(0xac100000, 12) || // 172.16.0.0/12 (Private)
    inCidr(0xc0000000, 24) || // 192.0.0.0/24 (IETF Protocol Assignments)
    inCidr(0xc0000200, 24) || // 192.0.2.0/24 (TEST-NET-1)
    inCidr(0xc0a80000, 16) || // 192.168.0.0/16 (Private)
    inCidr(0xc6120000, 15) || // 198.18.0.0/15 (Benchmarking)
    inCidr(0xc6336400, 24) || // 198.51.100.0/24 (TEST-NET-2)
    inCidr(0xcb007100, 24) || // 203.0.113.0/24 (TEST-NET-3)
    inCidr(0xe0000000, 4) || // 224.0.0.0/4 (Multicast)
    inCidr(0xf0000000, 4) // 240.0.0.0/4 (Reserved / Broadcast)
  ) {
    return false;
  }
  return true;
}

function expandIpv6(raw: string): number[] | null {
  const cleaned = raw.replace(/^\[|\]$/g, '').toLowerCase();
  if (cleaned.includes('%')) return null;
  let working = cleaned;
  const lastColon = working.lastIndexOf(':');
  if (lastColon >= 0 && working.slice(lastColon + 1).includes('.')) {
    const v4Int = parseIpv4Int(working.slice(lastColon + 1));
    if (v4Int === null) return null;
    const high = ((v4Int >>> 16) & 0xffff).toString(16);
    const low = (v4Int & 0xffff).toString(16);
    working = `${working.slice(0, lastColon)}:${high}:${low}`;
  }
  const halves = working.split('::');
  if (halves.length > 2) return null;
  const left = halves[0] ? halves[0].split(':') : [];
  const right = halves.length === 2 && halves[1] ? halves[1].split(':') : [];
  if (halves.length === 1 && left.length !== 8) return null;
  if (halves.length === 2 && left.length + right.length > 7) return null;
  const missing = halves.length === 2 ? 8 - (left.length + right.length) : 0;
  const words = [...left, ...Array(missing).fill('0'), ...right];
  if (words.length !== 8) return null;
  const groups: number[] = [];
  for (const word of words) {
    if (!/^[0-9a-f]{1,4}$/.test(word)) return null;
    groups.push(parseInt(word, 16));
  }
  return groups;
}

export function isGlobalIp(address: string): boolean {
  const cleaned = address.replace(/^\[|\]$/g, '');
  const version = isIP(cleaned);
  if (version === 4) {
    return isGlobalIpv4(cleaned);
  }
  if (version !== 6) {
    return false;
  }
  const groups = expandIpv6(cleaned);
  if (!groups) return false;
  const allZeroPrefix = groups.slice(0, 5).every(g => g === 0);
  // ::/128 (unspecified) or ::1/128 (loopback)
  if (allZeroPrefix && groups[5] === 0 && groups[6] === 0 && (groups[7] === 0 || groups[7] === 1)) {
    return false;
  }
  // ::ffff:0:0/96 (IPv4-mapped)
  if (allZeroPrefix && groups[5] === 0xffff) {
    const ip4 = `${(groups[6] >> 8) & 0xff}.${groups[6] & 0xff}.${(groups[7] >> 8) & 0xff}.${groups[7] & 0xff}`;
    return isGlobalIpv4(ip4);
  }
  // 64:ff9b:1::/48 (NAT64 local)
  if (groups[0] === 0x0064 && groups[1] === 0xff9b && groups[2] === 0x0001) return false;
  // 100::/64 (Discard-only)
  if (groups[0] === 0x0100 && groups[1] === 0 && groups[2] === 0 && groups[3] === 0) return false;
  // 2001::/23 (IETF protocol assignments, Teredo 2001::/32, Benchmarking 2001:2::/48, Documentation 2001:db8::/32, ORCHID)
  if (groups[0] === 0x2001) {
    if (groups[1] < 0x0200) {
      // 2001:1::1/128, 2001:1::2/128, 2001:3::/32, 2001:4:112::/48, 2001:20::/28, 2001:30::/28 are global exceptions in IANA
      return false;
    }
    if (groups[1] === 0x0db8) return false;
  }
  // 2002::/16 (6to4)
  if (groups[0] === 0x2002) return false;
  // fc00::/7 (Unique Local fc00..fdff)
  if ((groups[0] & 0xfe00) === 0xfc00) return false;
  // fe80::/10 (Link-Local fe80..febf)
  if ((groups[0] & 0xffc0) === 0xfe80) return false;
  // ff00::/8 (Multicast)
  if ((groups[0] & 0xff00) === 0xff00) return false;
  return true;
}

export type DnsResolver = (hostname: string) => Promise<string[]>;

export async function defaultDnsResolver(hostname: string): Promise<string[]> {
  let timer: NodeJS.Timeout | undefined;
  try {
    const records = await Promise.race([
      lookup(hostname, { all: true, verbatim: true }),
      new Promise<never>((_, reject) => {
        timer = setTimeout(() => reject(new Error('DNS timeout')), 5000);
      }),
    ]);
    return records.map(item => item.address);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

export class BrowserSecurityPolicy {
  resolver: DnsResolver;

  constructor(resolver: DnsResolver = defaultDnsResolver) {
    this.resolver = resolver;
  }

  async validate(url: string, grant: BrowserGrant): Promise<string> {
    if (!grant.enabled) {
      throw new BrowserError('permission_denied', 'Browser capability is disabled.');
    }
    const origin = originOf(url);
    const allowed = new Set(grant.origins.map(item => originOf(item)));
    const privateAllowed = new Set(grant.allow_private_origins.map(item => originOf(item)));
    const parsedOrigin = new URL(origin);
    const scheme = parsedOrigin.protocol.replace(/:$/, '');
    const publicHttps = grant.allow_public_https && scheme === 'https';

    if (scheme === 'http' && !allowed.has(origin) && grant.allow_public_https) {
      throw new BrowserError(
        'insecure_http_denied',
        'Use an HTTPS address. HTTP is only available for explicitly authorized local development sites.',
      );
    }
    if (!allowed.has(origin) && !publicHttps) {
      throw new BrowserError('origin_denied', 'The destination origin has not been authorized.');
    }

    const rawHostname = parsedOrigin.hostname.replace(/^\[|\]$/g, '');
    let addresses: string[];
    if (isIP(rawHostname) !== 0) {
      addresses = [rawHostname];
    } else {
      try {
        addresses = await this.resolver(rawHostname);
      } catch {
        throw new BrowserError('dns_failed', 'Could not safely resolve the destination.');
      }
    }
    if (!addresses || addresses.length === 0 || addresses.some(addr => isIP(addr.replace(/^\[|\]$/g, '')) === 0)) {
      throw new BrowserError('dns_failed', 'Destination has no resolved addresses.');
    }
    if (addresses.some(addr => !isGlobalIp(addr)) && !privateAllowed.has(origin)) {
      throw new BrowserError('private_network_denied', 'Private network access requires an exact origin grant.');
    }
    if (scheme === 'http' && addresses.some(addr => isGlobalIp(addr))) {
      throw new BrowserError(
        'insecure_http_denied',
        'Public websites require HTTPS; unencrypted HTTP connections are blocked.',
      );
    }
    return origin;
  }
}
