// Types for the virtual module provided by the gamestudio() Vite plugin.
// Add "@gamestudio/devserver/client" to "types" in your tsconfig.

declare module 'virtual:gamestudio/session' {
  /** Hub WebSocket port for games; null in production builds. */
  export const gamePort: number | null;
  /** Random per-server-start auth token; null in production builds. */
  export const token: string | null;
  /** `startScene` from gamestudio.json. */
  export const startScene: string;
  /** `name` from gamestudio.json. */
  export const name: string;
  /** `post` from gamestudio.json (screen effects for every scene), or null. */
  export const post: Record<string, unknown> | null;
  const session: {
    gamePort: number | null;
    token: string | null;
    startScene: string;
    name: string;
    post: Record<string, unknown> | null;
  };
  export default session;
}
