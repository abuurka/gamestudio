// Captures console output and uncaught errors for the live link (docs/SPEC.md §5.4).

export type LogLevel = 'debug' | 'info' | 'warn' | 'error';

export interface LogMessage {
  t: 'log';
  level: LogLevel;
  msg: string;
  source?: string;
}

export interface ErrorMessage {
  t: 'error';
  msg: string;
  stack?: string;
  file?: string;
  line?: number;
  col?: number;
}

export interface SourceLocation {
  file?: string;
  line?: number;
  col?: number;
}

const MAX_MSG = 8000;

/** The console methods as they were before capture was installed. */
export const originalConsole = {
  debug: console.debug.bind(console),
  log: console.log.bind(console),
  info: console.info.bind(console),
  warn: console.warn.bind(console),
  error: console.error.bind(console),
};

/** Formats console arguments into one string. */
export function formatArgs(args: unknown[]): string {
  const text = args
    .map((a) => {
      if (typeof a === 'string') return a;
      if (a instanceof Error) return a.stack ?? `${a.name}: ${a.message}`;
      if (a === undefined) return 'undefined';
      if (typeof a === 'function') return `[function ${a.name || 'anonymous'}]`;
      try {
        return JSON.stringify(a, (_k, v) => (typeof v === 'bigint' ? `${v}n` : v)) ?? String(a);
      } catch {
        return String(a);
      }
    })
    .join(' ');
  return text.length > MAX_MSG ? `${text.slice(0, MAX_MSG)}…` : text;
}

const FRAME_URL = /((?:https?|file):\/\/[^\s()]+?):(\d+):(\d+)/g;

/** Turns a served module URL into a project path: `/src/x.ts?t=1` → `src/x.ts`, `/@fs/F:/a.ts` → `F:/a.ts`. */
export function urlToFile(url: string): string {
  let path: string;
  try {
    path = decodeURIComponent(new URL(url).pathname);
  } catch {
    return url;
  }
  if (path.startsWith('/@fs/')) return path.slice(5);
  return path.replace(/^\/+/, '');
}

/**
 * Finds the most useful source location in a stack trace: the first frame in
 * the game's own `src/` folder, otherwise the first frame with a URL.
 */
export function parseStackLocation(stack: string | undefined): SourceLocation {
  if (!stack) return {};
  const frames: Required<SourceLocation>[] = [];
  for (const match of stack.matchAll(FRAME_URL)) {
    frames.push({ file: urlToFile(match[1]!), line: Number(match[2]), col: Number(match[3]) });
  }
  const own = frames.find((f) => f.file.startsWith('src/'));
  return own ?? frames[0] ?? {};
}

const PROGRAM_LOG = 'THREE.WebGLProgram: Program Info Log:';
const HLSL_WARNING = /^\(\d+,\d+(?:-\d+)?\): warning X\d+:/;

/**
 * True for a three.js program log made only of Direct3D shader-compiler warnings (e.g. X4122,
 * "cannot be represented accurately in double precision"): Chrome and Edge on Windows compile
 * WebGL through ANGLE/D3D11 and three.js prints any non-empty log. Harmless, and alarming in the
 * editor's Console, so it stays in the browser console only. Real shader errors are never matched.
 */
export function isShaderCompilerNoise(msg: string): boolean {
  if (!msg.startsWith(PROGRAM_LOG)) return false;
  const lines = msg.slice(PROGRAM_LOG.length).split('\n').map((l) => l.trim()).filter(Boolean);
  return lines.length > 0 && lines.every((l) => HLSL_WARNING.test(l));
}

/**
 * Wraps console.debug/log/info/warn/error; the originals still run.
 * Returns a function that restores the console.
 */
export function captureConsole(sink: (message: LogMessage) => void): () => void {
  const levels: Array<[keyof typeof originalConsole, LogLevel]> = [
    ['debug', 'debug'],
    ['log', 'info'],
    ['info', 'info'],
    ['warn', 'warn'],
    ['error', 'error'],
  ];
  let busy = false; // Guards against a sink that logs.
  for (const [method, level] of levels) {
    console[method] = (...args: unknown[]) => {
      originalConsole[method](...args);
      if (busy) return;
      busy = true;
      try {
        const msg = formatArgs(args);
        if (level === 'warn' && isShaderCompilerNoise(msg)) return;
        const loc = parseStackLocation(new Error().stack?.split('\n').slice(2).join('\n'));
        const source = loc.file ? `${loc.file}:${loc.line}:${loc.col}` : undefined;
        sink({ t: 'log', level, msg, ...(source && { source }) });
      } finally {
        busy = false;
      }
    };
  }
  return () => {
    for (const [method] of levels) console[method] = originalConsole[method];
  };
}

/** Converts anything thrown into an `error` message. */
export function toErrorMessage(error: unknown, fallback?: SourceLocation): ErrorMessage {
  const err = error instanceof Error ? error : undefined;
  const msg = err ? `${err.name}: ${err.message}` : formatArgs([error]);
  const stack = err?.stack;
  const loc = parseStackLocation(stack);
  const where = loc.file ? loc : fallback ?? {};
  return {
    t: 'error',
    msg,
    ...(stack && { stack }),
    ...(where.file && { file: where.file }),
    ...(where.line && { line: where.line }),
    ...(where.col && { col: where.col }),
  };
}

/** Listens for uncaught errors and unhandled promise rejections. */
export function captureErrors(sink: (message: ErrorMessage) => void): () => void {
  const onError = (event: ErrorEvent): void => {
    const fallback: SourceLocation = event.filename
      ? { file: urlToFile(event.filename), line: event.lineno, col: event.colno }
      : {};
    sink(toErrorMessage(event.error ?? event.message, fallback));
  };
  const onRejection = (event: PromiseRejectionEvent): void => {
    sink(toErrorMessage(event.reason));
  };
  window.addEventListener('error', onError);
  window.addEventListener('unhandledrejection', onRejection);
  return () => {
    window.removeEventListener('error', onError);
    window.removeEventListener('unhandledrejection', onRejection);
  };
}
