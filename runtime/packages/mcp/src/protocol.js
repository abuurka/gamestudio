// @ts-check
// SPDX-License-Identifier: MIT
// The Model Context Protocol over stdio: newline-delimited JSON-RPC 2.0 (no Content-Length
// headers), implemented directly instead of with @modelcontextprotocol/sdk. The server only
// offers tools, so the protocol surface is small (initialize, ping, tools/list, tools/call,
// cancellation), and doing it here keeps the package dependency-free apart from the browser
// driver, which matters because it is linked into every game project.

import { validate } from './schema.js';
import { ToolError, lineSplitter } from './util.js';

/** Newest first. A client asking for one of these gets it back; any other gets the newest. */
export const PROTOCOL_VERSIONS = ['2025-11-25', '2025-06-18', '2025-03-26', '2024-11-05'];

export const JSONRPC = {
  PARSE_ERROR: -32700,
  INVALID_REQUEST: -32600,
  METHOD_NOT_FOUND: -32601,
  INVALID_PARAMS: -32602,
  INTERNAL_ERROR: -32603,
};

/**
 * @typedef {{ type: 'text', text: string } | { type: 'image', data: string, mimeType: string }} Content
 * @typedef {{ content: Content[], isError?: boolean, structuredContent?: unknown }} CallToolResult
 * @typedef {{ signal: AbortSignal, progress: (progress: number, total?: number, message?: string) => void }} CallContext
 * @typedef {object} ToolDefinition
 * @property {string} name
 * @property {string} [title]
 * @property {string} description
 * @property {import('./schema.js').Schema} inputSchema
 * @property {{ readOnlyHint?: boolean, destructiveHint?: boolean, idempotentHint?: boolean, openWorldHint?: boolean }} [annotations]
 * @property {(args: any, call: CallContext) => Promise<unknown>} handler  its value goes through `toResult`
 */

const WITH_IMAGES = Symbol('withImages');

/**
 * A tool result with images: JSON text first, then the images (base64 PNG unless mimeType says).
 * @param {unknown} data
 * @param {{ data: string, mimeType?: string }[]} images
 */
export function withImages(data, images) {
  return { [WITH_IMAGES]: true, data, images };
}

/**
 * Turns a handler's return value into a CallToolResult: any JSON value becomes one text block
 * (an object with `ok: false` is marked isError); `withImages()` adds image blocks.
 * @param {unknown} value
 * @returns {CallToolResult}
 */
export function toResult(value) {
  /** @type {Content[]} */
  const content = [];
  let data = value;
  /** @type {{ data: string, mimeType?: string }[]} */
  let images = [];
  if (value && typeof value === 'object' && WITH_IMAGES in value) {
    data = /** @type {any} */ (value).data;
    images = /** @type {any} */ (value).images ?? [];
  }
  if (data !== undefined) content.push({ type: 'text', text: typeof data === 'string' ? data : JSON.stringify(data) });
  for (const image of images) content.push({ type: 'image', data: image.data, mimeType: image.mimeType ?? 'image/png' });
  const failed = data !== null && typeof data === 'object' && /** @type {any} */ (data).ok === false;
  return { content, ...(failed && { isError: true }) };
}

/** @param {unknown} error @returns {CallToolResult} */
export function errorResult(error) {
  const body =
    error instanceof ToolError
      ? error.toJSON()
      : {
          error: error instanceof Error ? `${error.name}: ${error.message}` : String(error),
          hint: 'Unexpected error inside gamestudio-mcp. Retry once; if it persists, call status and report the error.',
        };
  return { content: [{ type: 'text', text: JSON.stringify(body) }], isError: true };
}

/**
 * Creates a transport-independent MCP server. `handle(message)` resolves to the response
 * object (or null for notifications); `notify` is used for progress notifications.
 * @param {{ name: string, version: string, title?: string, instructions?: string,
 *   tools: ToolDefinition[], notify?: (message: object) => void,
 *   log?: (line: string) => void }} options
 */
export function createMcpServer(options) {
  const tools = new Map(options.tools.map((t) => [t.name, t]));
  /** @type {Map<string | number, AbortController>} */
  const inFlight = new Map();
  let clientInfo = /** @type {Record<string, unknown> | null} */ (null);
  let negotiated = /** @type {string | null} */ (null);
  const log = options.log ?? (() => {});

  /** @param {string | number | null} id @param {number} code @param {string} message @param {unknown} [data] */
  const error = (id, code, message, data) => ({ jsonrpc: '2.0', id, error: { code, message, ...(data !== undefined && { data }) } });
  /** @param {string | number} id @param {unknown} result */
  const ok = (id, result) => ({ jsonrpc: '2.0', id, result });

  function listTools() {
    return [...tools.values()].map((t) => ({
      name: t.name,
      ...(t.title && { title: t.title }),
      description: t.description,
      inputSchema: t.inputSchema,
      ...(t.annotations && { annotations: t.annotations }),
    }));
  }

  /**
   * @param {any} params
   * @param {string | number} id
   * @param {any} meta
   * @returns {Promise<CallToolResult>}
   */
  async function callTool(params, id, meta) {
    const name = params?.name;
    const tool = typeof name === 'string' ? tools.get(name) : undefined;
    if (!tool) {
      const known = [...tools.keys()];
      const close = known.filter((k) => typeof name === 'string' && (k.includes(name) || name.includes(k))).slice(0, 3);
      return errorResult(new ToolError(`Unknown tool '${String(name)}'.`, { hint: close.length ? `Did you mean: ${close.join(', ')}?` : `Tools: ${known.join(', ')}` }));
    }
    const args = params.arguments ?? {};
    const problems = validate(args, tool.inputSchema);
    if (problems.length > 0) {
      return errorResult(new ToolError(`Invalid arguments for ${tool.name}.`, { hint: 'Fix the arguments and call again.', data: { problems: problems.slice(0, 10) } }));
    }
    const controller = new AbortController();
    inFlight.set(id, controller);
    const progressToken = meta?.progressToken;
    /** @type {CallContext} */
    const call = {
      signal: controller.signal,
      progress: (progress, total, message) => {
        if (progressToken === undefined || !options.notify) return;
        options.notify({ jsonrpc: '2.0', method: 'notifications/progress', params: { progressToken, progress, ...(total !== undefined && { total }), ...(message && { message }) } });
      },
    };
    const started = Date.now();
    try {
      const result = toResult(await tool.handler(args, call));
      log(`${tool.name} ok in ${Date.now() - started} ms`);
      return result;
    } catch (err) {
      log(`${tool.name} failed in ${Date.now() - started} ms: ${err instanceof Error ? err.message : String(err)}`);
      return errorResult(err);
    } finally {
      inFlight.delete(id);
    }
  }

  /**
   * @param {unknown} message
   * @returns {Promise<object | null>}
   */
  async function handle(message) {
    if (!message || typeof message !== 'object' || Array.isArray(message)) {
      return error(null, JSONRPC.INVALID_REQUEST, 'Expected a JSON-RPC 2.0 object');
    }
    const msg = /** @type {any} */ (message);
    const isRequest = msg.id !== undefined && msg.id !== null;
    if (typeof msg.method !== 'string') {
      // A response to something we sent (we send no requests), or garbage.
      return isRequest && msg.result === undefined && msg.error === undefined ? error(msg.id, JSONRPC.INVALID_REQUEST, 'Missing method') : null;
    }
    const params = msg.params ?? {};
    if (!isRequest) {
      if (msg.method === 'notifications/cancelled') inFlight.get(params.requestId)?.abort(params.reason ?? 'cancelled');
      return null; // notifications/initialized and any other notification
    }
    switch (msg.method) {
      case 'initialize': {
        const requested = params.protocolVersion;
        negotiated = PROTOCOL_VERSIONS.includes(requested) ? requested : PROTOCOL_VERSIONS[0];
        clientInfo = params.clientInfo ?? null;
        log(`initialize from ${JSON.stringify(clientInfo)} asking ${requested}; using ${negotiated}`);
        return ok(msg.id, {
          protocolVersion: negotiated,
          capabilities: { tools: { listChanged: false } },
          serverInfo: { name: options.name, version: options.version, ...(options.title && { title: options.title }) },
          ...(options.instructions && { instructions: options.instructions }),
        });
      }
      case 'ping':
        return ok(msg.id, {});
      case 'tools/list':
        return ok(msg.id, { tools: listTools() });
      case 'tools/call':
        return ok(msg.id, await callTool(params, msg.id, params._meta));
      case 'resources/list':
        return ok(msg.id, { resources: [] });
      case 'resources/templates/list':
        return ok(msg.id, { resourceTemplates: [] });
      case 'prompts/list':
        return ok(msg.id, { prompts: [] });
      case 'logging/setLevel':
        return ok(msg.id, {});
      default:
        return error(msg.id, JSONRPC.METHOD_NOT_FOUND, `Method not found: ${msg.method}`);
    }
  }

  return {
    handle,
    listTools,
    get clientInfo() {
      return clientInfo;
    },
    get protocolVersion() {
      return negotiated;
    },
    cancelAll() {
      for (const controller of inFlight.values()) controller.abort('shutdown');
    },
  };
}

/**
 * Serves an MCP server over a pair of streams (stdin/stdout). Resolves when input ends.
 * Everything else the process prints must go to stderr: stdout carries only protocol messages.
 * @param {ReturnType<typeof createMcpServer>} server
 * @param {{ input: NodeJS.ReadableStream, output: NodeJS.WritableStream, log?: (line: string) => void }} streams
 */
export function serveStdio(server, { input, output, log = () => {} }) {
  /** @param {object} message */
  const write = (message) => {
    output.write(`${JSON.stringify(message)}\n`);
  };
  return new Promise((resolve) => {
    input.setEncoding?.('utf8');
    const onLine = lineSplitter((line) => {
      let message;
      try {
        message = JSON.parse(line);
      } catch {
        write({ jsonrpc: '2.0', id: null, error: { code: JSONRPC.PARSE_ERROR, message: 'Invalid JSON' } });
        return;
      }
      const batch = Array.isArray(message) ? message : [message];
      for (const item of batch) {
        server
          .handle(item)
          .then((response) => {
            if (response) write(response);
          })
          .catch((err) => {
            log(`internal error: ${err instanceof Error ? err.stack : String(err)}`);
            if (item && typeof item === 'object' && 'id' in item) {
              write({ jsonrpc: '2.0', id: item.id, error: { code: JSONRPC.INTERNAL_ERROR, message: String(err) } });
            }
          });
      }
    });
    input.on('data', (chunk) => onLine(String(chunk)));
    input.on('end', () => resolve(undefined));
    input.on('close', () => resolve(undefined));
  });
}
