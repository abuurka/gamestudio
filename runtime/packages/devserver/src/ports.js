// @ts-check
// Listening with port fallback: if a port is busy, try the next ones.

/**
 * Listens on `port` (or the next free one among `attempts` ports) and resolves
 * with the bound port. Port 0 asks the OS for an ephemeral port.
 *
 * @param {import('node:net').Server} server
 * @param {number} port
 * @param {string} host
 * @param {number} [attempts] total ports to try (default 11: the port and the next 10)
 * @returns {Promise<number>}
 */
export async function listenWithFallback(server, port, host, attempts = 11) {
  const tries = port === 0 ? 1 : attempts;
  /** @type {unknown} */
  let lastError;
  for (let i = 0; i < tries; i++) {
    try {
      await listenOnce(server, port === 0 ? 0 : port + i, host);
      const address = server.address();
      if (address && typeof address === 'object') return address.port;
      throw new Error('Server has no address after listening');
    } catch (error) {
      lastError = error;
      const code = /** @type {NodeJS.ErrnoException} */ (error).code;
      if (code !== 'EADDRINUSE' && code !== 'EACCES') throw error;
    }
  }
  throw new Error(`No free port in ${port}-${port + tries - 1} on ${host}: ${String(lastError)}`);
}

/**
 * @param {import('node:net').Server} server
 * @param {number} port
 * @param {string} host
 * @returns {Promise<void>}
 */
function listenOnce(server, port, host) {
  return new Promise((resolve, reject) => {
    /** @param {Error} error */
    const onError = (error) => {
      server.off('listening', onListening);
      reject(error);
    };
    const onListening = () => {
      server.off('error', onError);
      resolve();
    };
    server.once('error', onError);
    server.once('listening', onListening);
    server.listen(port, host);
  });
}
