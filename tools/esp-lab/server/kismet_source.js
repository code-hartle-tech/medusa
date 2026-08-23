// Kismet remote-capture datasource.
//
// Lets an authorised Medusa sensor feed a Kismet server the operator already
// runs, so Medusa becomes one source in a larger picture rather than a
// separate silo with its own incompatible view.
//
// TRANSPORT CHOICE IS A SECURITY DECISION, NOT A PREFERENCE.
//
// Kismet offers two remote-capture transports. The legacy one is a raw TCP
// socket on 3501 with *no authentication at all*, relying on the operator
// having tunnelled it. This module implements only the WebSocket transport on
// the normal Kismet port, authenticated with an API key carrying the
// `datasource` role.
//
// The unauthenticated mode is deliberately not implemented and must not be
// added as a fallback. This project's own position is that any wireless
// retrieval path must be authenticated and bonded; shipping an unauthenticated
// remote link because it was easier would contradict that in the one place it
// matters most — the path that carries capture data off the device.
//
// VERIFICATION STATUS. Written against Kismet's documented remote-capture
// protocol and verified against a conforming mock in the test suite. It has
// NOT been run against a real Kismet server, because Kismet does not install
// on this workstation. Treat "the mock accepts it" as evidence about this
// client, not as integration proof.

import { WebSocket } from 'ws';

export const KISMET_DEFAULT_PORT = 2501;

/** Reasons a connection attempt was refused, as stable codes. */
export const KISMET_ERRORS = {
  CONFIG: 'kismet.config',
  AUTH: 'kismet.auth',
  TRANSPORT: 'kismet.transport',
  PROTOCOL: 'kismet.protocol',
};

/**
 * Validate connection settings before opening anything.
 *
 * Separated from the connection so the companion can tell an operator what is
 * wrong with their settings without a network round trip, and so this is
 * testable without a server.
 */
export function validateKismetConfig(config = {}) {
  const { host, port = KISMET_DEFAULT_PORT, apiKey, sourceName, insecure } = config;

  if (typeof host !== 'string' || !host.trim()) {
    return { ok: false, error: KISMET_ERRORS.CONFIG, detail: 'a host is required' };
  }
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    return { ok: false, error: KISMET_ERRORS.CONFIG, detail: 'port must be 1-65535' };
  }
  if (typeof apiKey !== 'string' || apiKey.length < 8) {
    return { ok: false, error: KISMET_ERRORS.AUTH, detail: 'an API key with the datasource role is required' };
  }
  if (typeof sourceName !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$/.test(sourceName)) {
    return { ok: false, error: KISMET_ERRORS.CONFIG, detail: 'source name must be short and printable' };
  }
  // Refusing to send an API key over plaintext to anything but loopback is the
  // whole point of choosing the authenticated transport.
  const loopback = /^(localhost|127\.0\.0\.1|::1)$/i.test(host.trim());
  if (!insecure && !loopback && !config.tls) {
    return {
      ok: false,
      error: KISMET_ERRORS.CONFIG,
      detail: 'refusing to send an API key in plaintext to a remote host; enable TLS or set insecure explicitly',
    };
  }
  return { ok: true };
}

/** The URL a validated config produces. Pure, so a test can assert it. */
export function kismetUrl(config) {
  const scheme = config.tls ? 'wss' : 'ws';
  const port = config.port ?? KISMET_DEFAULT_PORT;
  return `${scheme}://${config.host}:${port}/datasource/remote/remotesource.ws`;
}

/**
 * Open an authenticated datasource connection.
 *
 * `open()` resolves once the server has accepted the source announcement, not
 * merely once the socket is up — an accepted socket that then rejects the
 * source looks identical to success otherwise.
 */
export class KismetDatasource {
  constructor(config, hooks = {}) {
    this.config = config;
    this.hooks = hooks;
    this.socket = null;
    this.accepted = false;
    this.sent = 0;
  }

  open({ WebSocketImpl = WebSocket, timeoutMs = 10000 } = {}) {
    const check = validateKismetConfig(this.config);
    if (!check.ok) return Promise.reject(Object.assign(new Error(check.detail), { code: check.error }));

    return new Promise((resolve, reject) => {
      const url = kismetUrl(this.config);
      // The key travels as a header rather than a query parameter: query
      // strings are logged by proxies and land in history.
      const socket = new WebSocketImpl(url, {
        headers: { Authorization: `Bearer ${this.config.apiKey}` },
      });
      this.socket = socket;

      const timer = setTimeout(() => {
        socket.close?.();
        reject(Object.assign(new Error('kismet did not accept the source in time'), { code: KISMET_ERRORS.TRANSPORT }));
      }, timeoutMs);

      const settleError = (code, message) => {
        clearTimeout(timer);
        reject(Object.assign(new Error(message), { code }));
      };

      socket.on('open', () => {
        // Announce what this source is before sending any packet.
        socket.send(JSON.stringify({
          type: 'source.announce',
          name: this.config.sourceName,
          definition: `medusa:name=${this.config.sourceName}`,
          uuid: this.config.uuid ?? undefined,
        }));
      });

      socket.on('message', (raw) => {
        let payload;
        try {
          payload = JSON.parse(String(raw));
        } catch {
          return settleError(KISMET_ERRORS.PROTOCOL, 'kismet sent something that is not JSON');
        }
        if (payload.type === 'source.accepted') {
          clearTimeout(timer);
          this.accepted = true;
          this.hooks.onAccepted?.(payload);
          return resolve(payload);
        }
        if (payload.type === 'source.rejected') {
          return settleError(KISMET_ERRORS.PROTOCOL, payload.reason || 'kismet rejected the source');
        }
        this.hooks.onMessage?.(payload);
      });

      socket.on('unexpected-response', (_req, res) => {
        // 401 here means the key is missing the datasource role, which is a
        // different fix from "the host is wrong" and deserves its own code.
        const code = res?.statusCode === 401 || res?.statusCode === 403
          ? KISMET_ERRORS.AUTH
          : KISMET_ERRORS.TRANSPORT;
        settleError(code, `kismet replied ${res?.statusCode ?? 'an error'}`);
      });

      socket.on('error', (error) => settleError(KISMET_ERRORS.TRANSPORT, error.message));
      socket.on('close', () => {
        this.accepted = false;
        this.hooks.onClose?.();
      });
    });
  }

  /**
   * Forward one observation.
   *
   * Refuses before acceptance rather than buffering: a sensor that queues
   * packets against a source the server never accepted will eventually deliver
   * a burst of stale observations with misleading timestamps.
   */
  sendPacket(packet) {
    if (!this.accepted || !this.socket) return false;
    this.socket.send(JSON.stringify({ type: 'source.packet', packet }));
    this.sent += 1;
    return true;
  }

  close() {
    this.accepted = false;
    this.socket?.close?.();
    this.socket = null;
  }
}
