import Ajv2020 from 'ajv/dist/2020.js';
import { z } from 'zod';
import schema from '../wire.schema.json' with { type: 'json' };
import type {
  BrowserPreviewParams,
  DesktopEvent,
  DesktopHostMethods,
  DesktopMethods,
} from './wire.js';
export type * from './wire.js';

const ajv = new Ajv2020({ strict: false, allErrors: false });
ajv.addSchema(schema);

function checked<T>(name: string, value: unknown): T {
  const validate = ajv.getSchema(`${schema.$id}#/$defs/${name}`);
  if (!validate || !validate(value)) throw new Error('Masaüstü iletişim verisi doğrulanamadı.');
  return value as T;
}

export type Method = keyof DesktopMethods;
export type HostMethod = keyof DesktopHostMethods;

export function parseParams<M extends Method>(method: M, value: unknown): DesktopMethods[M]['params'] {
  return checked(schema['x-methods'][method].params, value);
}
export function parseResult<M extends Method>(method: M, value: unknown): DesktopMethods[M]['result'] {
  return checked(schema['x-methods'][method].result, value);
}
export function parseHostParams<M extends HostMethod>(method: M, value: unknown): DesktopHostMethods[M]['params'] {
  return checked(schema['x-host-methods'][method].params, value);
}
export function parseHostResult<M extends HostMethod>(method: M, value: unknown): DesktopHostMethods[M]['result'] {
  return checked(schema['x-host-methods'][method].result, value);
}
export const parsePreviewParams = (value: unknown): BrowserPreviewParams => checked('BrowserPreviewParams', value);
export const parseEvent = (value: unknown): DesktopEvent => checked('DesktopEvent', value);
export const MAX_FRAME_BYTES = 2 * 1024 * 1024;
export const PROTOCOL_VERSION = 1;

export const rpcResponse = z.object({
  jsonrpc: z.literal('2.0'),
  id: z.union([z.string(), z.number().int(), z.null()]),
  result: z.unknown().optional(),
  error: z.object({
    code: z.number().int(),
    message: z.string().max(4096),
    data: z.record(z.string(), z.unknown()).optional(),
  }).strict().optional(),
}).strict().refine(value => Object.hasOwn(value, 'result') !== Object.hasOwn(value, 'error'));

export const rpcNotification = z.object({
  jsonrpc: z.literal('2.0'), method: z.literal('desktop.event'), params: z.unknown(),
}).strict();

export const rpcHostRequest = z.object({
  jsonrpc: z.literal('2.0'),
  id: z.union([z.string(), z.number().int()]),
  method: z.enum(Object.keys(schema['x-host-methods']) as [HostMethod, ...HostMethod[]]),
  params: z.unknown(),
}).strict();
