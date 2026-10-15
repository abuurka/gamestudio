import { PerspectiveCamera, Vector3 } from 'three';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { Hud, projectToScreen } from '../src/hud/hud';
import { installDom, type FakeDom, type FakeElement } from './dom';

describe('HUD labels pinned to the world', () => {
  it('projects world points to canvas pixels and hides points behind the camera', () => {
    const camera = new PerspectiveCamera(60, 2, 0.1, 100);
    camera.position.set(0, 0, 10);
    camera.lookAt(0, 0, 0);
    camera.updateProjectionMatrix();
    expect(projectToScreen(new Vector3(0, 0, 0), camera, 800, 400)).toEqual({ x: 400, y: 200 });
    const above = projectToScreen(new Vector3(0, 2, 0), camera, 800, 400)!;
    expect(above.x).toBeCloseTo(400, 6);
    expect(above.y).toBeLessThan(200); // Screen y grows downwards.
    const right = projectToScreen(new Vector3(3, 0, 0), camera, 800, 400)!;
    expect(right.x).toBeGreaterThan(400);
    expect(projectToScreen(new Vector3(0, 0, 20), camera, 800, 400)).toBeNull(); // Behind the camera.
  });
});

describe('HUD layout and timing', () => {
  let dom: FakeDom;
  beforeEach(() => {
    dom = installDom({}, [480, 270]);
  });
  afterEach(() => dom.restore());

  const regionOf = (hud: Hud, text: string) =>
    (hud.element as unknown as FakeElement).all().find((e) => e.textContent === text)!.parentElement!;

  it('lets centred text use the full width before wrapping', () => {
    const hud = new Hud(dom.canvas as unknown as HTMLCanvasElement);
    hud.set('win', 'All gems found!\nPress R to play again', { position: 'center', size: 'large' });
    hud.set('timer', 'Time 1:30', { position: 'top-center' });
    for (const text of ['All gems found!\nPress R to play again', 'Time 1:30']) {
      const css = regionOf(hud, text).style.cssText;
      expect(css).toContain('width:max-content');
      expect(css).toContain('max-width:calc(100% - 32px)');
      expect(css).toContain('left:50%');
    }
  });

  it('expires durations on its clock and accepts points as arrays or objects', () => {
    let now = 0;
    const hud = new Hud(dom.canvas as unknown as HTMLCanvasElement, () => now);
    hud.set('a', 'array', { at: [1, 2, 3], duration: 1 });
    hud.set('b', 'object', { at: new Vector3(1, 2, 3), offset: { x: 0, y: 1, z: 0 } });
    const texts = () => (hud.element as unknown as FakeElement).all().map((e) => e.textContent);
    now = 999;
    hud.expire();
    expect(texts()).toEqual(['array', 'object']);
    now = 1000;
    hud.expire();
    expect(texts()).toEqual(['object']);
  });
});
