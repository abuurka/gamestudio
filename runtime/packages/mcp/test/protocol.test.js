import { PassThrough } from 'node:stream';
import { describe, expect, it } from 'vitest';
import { PROTOCOL_VERSIONS, createMcpServer, serveStdio, toResult, withImages } from '../src/protocol.js';
import { validate } from '../src/schema.js';
import { ToolError } from '../src/util.js';

const echo = {
  name: 'echo',
  description: 'Echoes its input.',
  inputSchema: { type: 'object', properties: { text: { type: 'string' }, n: { type: 'integer', minimum: 1 } }, required: ['text'], additionalProperties: false },
  annotations: { readOnlyHint: true },
  handler: async (args) => ({ echoed: args.text }),
};
const failing = {
  name: 'fails',
  description: 'Always fails for the agent.',
  inputSchema: { type: 'object', properties: {}, additionalProperties: false },
  handler: async () => {
    throw new ToolError('Blender is not running.', { code: 'blender_not_running', hint: 'Call launch_blender.' });
  },
};
const slow = {
  name: 'slow',
  description: 'Waits until cancelled.',
  inputSchema: { type: 'object', properties: {}, additionalProperties: false },
  handler: (_args, call) =>
    new Promise((_resolve, reject) => call.signal.addEventListener('abort', () => reject(new ToolError('cancelled', { code: 'cancelled' })))),
};

function server() {
  return createMcpServer({ name: 'test', version: '1.2.3', instructions: 'Use it well.', tools: [echo, failing, slow] });
}
const request = (id, method, params) => ({ jsonrpc: '2.0', id, method, params });

describe('MCP protocol', () => {
  it('negotiates the protocol version', async () => {
    const s = server();
    const r = await s.handle(request(1, 'initialize', { protocolVersion: '2025-06-18', capabilities: {}, clientInfo: { name: 'c', version: '1' } }));
    expect(r.result).toMatchObject({ protocolVersion: '2025-06-18', capabilities: { tools: { listChanged: false } }, serverInfo: { name: 'test', version: '1.2.3' }, instructions: 'Use it well.' });
    const newer = await s.handle(request(2, 'initialize', { protocolVersion: '2099-01-01' }));
    expect(newer.result.protocolVersion).toBe(PROTOCOL_VERSIONS[0]);
    expect(PROTOCOL_VERSIONS).toContain('2025-11-25');
  });

  it('answers ping, lists tools and ignores notifications', async () => {
    const s = server();
    expect(await s.handle(request(1, 'ping'))).toEqual({ jsonrpc: '2.0', id: 1, result: {} });
    const list = await s.handle(request(2, 'tools/list'));
    expect(list.result.tools.map((t) => t.name)).toEqual(['echo', 'fails', 'slow']);
    expect(list.result.tools[0]).toEqual({ name: 'echo', description: echo.description, inputSchema: echo.inputSchema, annotations: { readOnlyHint: true } });
    expect(await s.handle({ jsonrpc: '2.0', method: 'notifications/initialized' })).toBeNull();
    expect((await s.handle(request(3, 'resources/list'))).result).toEqual({ resources: [] });
    expect((await s.handle(request(4, 'nope/nope'))).error.code).toBe(-32601);
    expect((await s.handle('garbage')).error.code).toBe(-32600);
  });

  it('calls tools and reports errors as tool results, not protocol errors', async () => {
    const s = server();
    const ok = await s.handle(request(1, 'tools/call', { name: 'echo', arguments: { text: 'hi' } }));
    expect(ok.result).toEqual({ content: [{ type: 'text', text: '{"echoed":"hi"}' }] });

    const bad = await s.handle(request(2, 'tools/call', { name: 'echo', arguments: { text: 3, extra: true } }));
    expect(bad.result.isError).toBe(true);
    const body = JSON.parse(bad.result.content[0].text);
    expect(body.error).toBe('Invalid arguments for echo.');
    expect(body.problems).toEqual(["arguments: unknown argument 'extra' (known: text, n)", 'arguments.text: expected string, got number']);

    const agentError = await s.handle(request(3, 'tools/call', { name: 'fails', arguments: {} }));
    expect(agentError.result.isError).toBe(true);
    expect(JSON.parse(agentError.result.content[0].text)).toEqual({ error: 'Blender is not running.', code: 'blender_not_running', hint: 'Call launch_blender.' });

    const unknown = await s.handle(request(4, 'tools/call', { name: 'ech', arguments: {} }));
    expect(JSON.parse(unknown.result.content[0].text).hint).toContain('echo');
  });

  it('cancels a running call on notifications/cancelled', async () => {
    const s = server();
    const pending = s.handle(request(7, 'tools/call', { name: 'slow', arguments: {} }));
    await s.handle({ jsonrpc: '2.0', method: 'notifications/cancelled', params: { requestId: 7, reason: 'user' } });
    const r = await pending;
    expect(r.result.isError).toBe(true);
    expect(JSON.parse(r.result.content[0].text).code).toBe('cancelled');
  });

  it('turns values into content blocks', () => {
    expect(toResult({ ok: false, error: 'x' })).toEqual({ content: [{ type: 'text', text: '{"ok":false,"error":"x"}' }], isError: true });
    expect(toResult('plain')).toEqual({ content: [{ type: 'text', text: 'plain' }] });
    expect(toResult(withImages({ n: 1 }, [{ data: 'AAAA' }]))).toEqual({
      content: [
        { type: 'text', text: '{"n":1}' },
        { type: 'image', data: 'AAAA', mimeType: 'image/png' },
      ],
    });
  });

  it('speaks newline-delimited JSON-RPC over streams', async () => {
    const input = new PassThrough();
    const output = new PassThrough();
    const lines = [];
    output.setEncoding('utf8');
    output.on('data', (chunk) => lines.push(...String(chunk).split('\n').filter(Boolean)));
    const done = serveStdio(server(), { input, output });
    input.write(`${JSON.stringify(request(1, 'ping'))}\n{broken\n`);
    input.write(`${JSON.stringify(request(2, 'tools/call', { name: 'echo', arguments: { text: 'a' } })).slice(0, 20)}`);
    input.write(`${JSON.stringify(request(2, 'tools/call', { name: 'echo', arguments: { text: 'a' } })).slice(20)}\n`);
    await new Promise((r) => setTimeout(r, 50));
    input.end();
    await done;
    const messages = lines.map((l) => JSON.parse(l));
    expect(messages).toContainEqual({ jsonrpc: '2.0', id: 1, result: {} });
    expect(messages).toContainEqual({ jsonrpc: '2.0', id: null, error: { code: -32700, message: 'Invalid JSON' } });
    expect(messages.find((m) => m.id === 2).result.content[0].text).toBe('{"echoed":"a"}');
  });
});

describe('argument validation', () => {
  it('checks types, ranges, enums, arrays and unions', () => {
    const schema = {
      type: 'object',
      properties: {
        names: { type: ['string', 'array'], items: { type: 'string' }, minItems: 1 },
        size: { type: 'integer', minimum: 64, maximum: 2048 },
        shading: { type: 'string', enum: ['solid', 'material'] },
        vec: { type: 'array', items: { type: 'number' }, minItems: 3, maxItems: 3 },
      },
      required: ['names'],
      additionalProperties: false,
    };
    expect(validate({ names: 'Crate' }, schema)).toEqual([]);
    expect(validate({ names: ['A', 'B'], size: 512, shading: 'solid', vec: [1, 2, 3] }, schema)).toEqual([]);
    expect(validate({}, schema)).toEqual(["arguments: missing required 'names'"]);
    expect(validate({ names: [] }, schema)).toEqual(['arguments.names: needs at least 1 item(s)']);
    expect(validate({ names: [1] }, schema)).toEqual(['arguments.names[0]: expected string, got number']);
    expect(validate({ names: 'a', size: 10.5 }, schema)).toEqual(['arguments.size: expected integer, got number']);
    expect(validate({ names: 'a', size: 4096 }, schema)).toEqual(['arguments.size: must be <= 2048']);
    expect(validate({ names: 'a', shading: 'wire' }, schema)[0]).toContain('must be one of "solid", "material"');
    expect(validate({ names: 'a', vec: [1, 2] }, schema)).toEqual(['arguments.vec: needs at least 3 item(s)']);
  });
});
