#!/usr/bin/env node
// @ts-check
// SPDX-License-Identifier: MIT
// gamestudio-mcp: the GameStudio MCP server on stdio. The coding agent starts it with the project as
// the working directory: Claude Code from the project's .mcp.json, Codex from its .codex/config.toml
// ([mcp_servers.gamestudio], the same command and args):
//
//   { "mcpServers": { "gamestudio": { "command": "node",
//       "args": ["node_modules/@gamestudio/mcp/bin/gamestudio-mcp.js"] } } }
//
// Options: --project <dir> (default: the working directory or $GAMESTUDIO_PROJECT),
//          --log <file> (also $GAMESTUDIO_MCP_LOG), --version, --help.
// stdout carries only MCP messages; diagnostics go to stderr and the log file.

import { appendFileSync, mkdirSync } from 'node:fs';
import path from 'node:path';
import { findProjectRoot } from '../src/discovery.js';
import { VERSION, createGameStudioServer, serveStdio } from '../src/index.js';

const argv = process.argv.slice(2);
/** @param {string} name */
const option = (name) => {
  const i = argv.indexOf(name);
  return i >= 0 ? argv[i + 1] : undefined;
};

if (argv.includes('--version')) {
  process.stdout.write(`${VERSION}\n`);
  process.exit(0);
}
if (argv.includes('--help')) {
  process.stdout.write('gamestudio-mcp [--project <dir>] [--log <file>]\nMCP server (stdio) for GameStudio projects. See docs/MCP.md.\n');
  process.exit(0);
}

const start = option('--project') ?? process.env.GAMESTUDIO_PROJECT ?? process.cwd();
const root = findProjectRoot(start);
const logFile = option('--log') ?? process.env.GAMESTUDIO_MCP_LOG ?? null;
if (logFile) mkdirSync(path.dirname(path.resolve(logFile)), { recursive: true });

/** @param {string} line */
function log(line) {
  const text = `[gamestudio-mcp ${new Date().toISOString()}] ${line}\n`;
  process.stderr.write(text);
  if (logFile) {
    try {
      appendFileSync(logFile, text);
    } catch {
      // logging must never break the server
    }
  }
}

const { server, ctx } = createGameStudioServer({
  root,
  log,
  notify: (message) => process.stdout.write(`${JSON.stringify(message)}\n`),
});
log(`v${VERSION} serving ${root ?? `no project (no gamestudio.json at or above ${start})`}`);
ctx.hub.watch();

let stopping = false;
async function shutdown(/** @type {string} */ why) {
  if (stopping) return;
  stopping = true;
  log(`shutting down (${why})`);
  server.cancelAll();
  const timer = setTimeout(() => process.exit(0), 8000);
  timer.unref();
  try {
    await ctx.shutdown();
  } finally {
    process.exit(0);
  }
}

process.on('SIGINT', () => void shutdown('SIGINT'));
process.on('SIGTERM', () => void shutdown('SIGTERM'));
process.on('uncaughtException', (error) => log(`uncaught: ${error.stack ?? error}`));
process.on('unhandledRejection', (error) => log(`unhandled rejection: ${error instanceof Error ? error.stack : String(error)}`));

await serveStdio(server, { input: process.stdin, output: process.stdout, log });
await shutdown('stdin closed');
