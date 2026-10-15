// The dev-server session, provided by the `gamestudio()` Vite plugin.
// In production builds `gamePort` and `token` are null, which disables the link.
import session from 'virtual:gamestudio/session';

export interface LinkSession {
  gamePort: number | null;
  token: string | null;
  startScene: string;
  /** The project name (gamestudio.json "name"); older dev servers leave it out. */
  name?: string;
  /** gamestudio.json "post": screen effects for every scene. */
  post?: Record<string, unknown> | null;
}

export const linkSession: LinkSession = session;
