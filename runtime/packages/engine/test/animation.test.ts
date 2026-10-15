import {
  AnimationClip,
  Bone,
  BufferGeometry,
  Group,
  Mesh,
  Object3D,
  Quaternion,
  QuaternionKeyframeTrack,
  Skeleton,
  SkinnedMesh,
  VectorKeyframeTrack,
} from 'three';
import { describe, expect, it } from 'vitest';
import { AnimationService } from '../src/animation/animation';
import { World } from '../src/ecs/world';
import { replaceComponents } from '../src/game/edits';
import { Animator } from '../src/schema';

/** y goes 0 → 2 over one second. */
const rise = (node: string, name = 'Rise') =>
  new AnimationClip(name, 1, [new VectorKeyframeTrack(`${node}.position`, [0, 1], [0, 0, 0, 0, 2, 0])]);
/** x goes 0 → 4 over two seconds. */
const slide = (node: string, name = 'Slide') =>
  new AnimationClip(name, 2, [new VectorKeyframeTrack(`${node}.position`, [0, 2], [0, 0, 0, 4, 0, 0])]);

function setup(clips: AnimationClip[], animator?: Record<string, unknown>) {
  const world = new World();
  const box = new Object3D();
  box.name = 'Box';
  const e = world.spawn({ id: 'gs_box', object3d: box, components: animator ? { Animator: animator } : {} });
  const warnings: string[] = [];
  const animation = new AnimationService((m) => warnings.push(m));
  animation.reset(world, clips);
  return { world, e, box, animation, warnings };
}

describe('Animator component', () => {
  it('plays its clip on start and loops it', () => {
    const { e, box, animation } = setup([rise('Box')], { clip: 'Rise' });
    animation.update(0); // First sight: starts playing.
    expect(animation.isPlaying(e)).toBe(true);
    expect(animation.isPlaying(e, 'Rise')).toBe(true);
    expect(animation.current(e)).toBe('Rise');
    animation.update(0.5);
    expect(box.position.y).toBeCloseTo(1, 5);
    animation.update(0.75); // Wraps: 1.25 s → 0.25 s.
    expect(box.position.y).toBeCloseTo(0.5, 5);
    expect(animation.events).toEqual([{ type: 'loop', entity: e, clip: 'Rise' }]);
  });

  it('defaults to the first clip that animates the entity, and respects playOnStart and speed', () => {
    const idle = setup([rise('Other', 'NotMine'), slide('Box')], {});
    idle.animation.update(0);
    expect(idle.animation.current(idle.e)).toBe('Slide');
    expect(idle.animation.clips(idle.e)).toEqual(['Slide']);
    expect(idle.animation.clips()).toEqual(['NotMine', 'Slide']);

    const off = setup([rise('Box')], { clip: 'Rise', playOnStart: false });
    off.animation.update(0.5);
    expect(off.animation.isPlaying(off.e)).toBe(false);
    expect(off.box.position.y).toBe(0);

    const fast = setup([rise('Box')], { clip: 'Rise', speed: 2 });
    fast.animation.update(0);
    fast.animation.update(0.25);
    expect(fast.box.position.y).toBeCloseTo(1, 5);
  });

  it('plays once, holds the last frame and reports "finished"', () => {
    const { e, box, animation } = setup([rise('Box')], { clip: 'Rise', loop: false });
    animation.update(0);
    animation.update(0.6);
    expect(animation.events).toEqual([]);
    animation.update(0.6);
    expect(animation.events).toEqual([{ type: 'finished', entity: e, clip: 'Rise' }]);
    expect(animation.isPlaying(e)).toBe(false);
    expect(box.position.y).toBeCloseTo(2, 5);
    animation.update(0.5);
    expect(animation.events).toEqual([]); // Events last one frame.
    expect(box.position.y).toBeCloseTo(2, 5);
  });

  it('reacts to edits of its fields (live link or code)', () => {
    const { e, box, animation } = setup([rise('Box'), slide('Box')], { clip: 'Rise', fade: 0 });
    animation.update(0);
    animation.update(0.5);
    replaceComponents(e, { Animator: { clip: 'Slide', fade: 0 } }); // As `entity.components` does.
    animation.update(0);
    expect(animation.current(e)).toBe('Slide');
    animation.update(1);
    expect(box.position.x).toBeCloseTo(2, 5);

    e.get(Animator).speed = 0.5;
    animation.update(1);
    expect(box.position.x).toBeCloseTo(3, 5);

    e.get(Animator).clip = '';
    animation.update(0);
    expect(animation.isPlaying(e)).toBe(false);
    expect(box.position.x).toBe(0); // Stopping restores the rest pose.

    replaceComponents(e, {}); // Removing the Animator stops it too.
    expect(animation.current(e)).toBeNull();
  });

  it('crossfades between clips and stops the old one when the fade ends', () => {
    const riseClip = rise('Box');
    const { e, box, animation } = setup([riseClip, slide('Box')], { clip: 'Rise' });
    animation.update(0);
    animation.update(1); // Rise wrapped to 0 → y = 0.
    expect(animation.play(e, 'Slide', { fade: 0.5 })).toBe(true);
    animation.update(0.25);
    const rising = animation.mixer(e).existingAction(riseClip)!;
    expect(rising.getEffectiveWeight()).toBeGreaterThan(0);
    expect(rising.getEffectiveWeight()).toBeLessThan(1);
    animation.update(0.5);
    expect(rising.isRunning()).toBe(false);
    expect(animation.mixer(e).existingAction(riseClip)).toBe(rising);
    expect(box.position.y).toBeCloseTo(0, 5);
    expect(box.position.x).toBeCloseTo(1.5, 5);
  });
});

describe('ctx.animation', () => {
  it('plays clips on entities without an Animator and binds only their own tracks', () => {
    const world = new World();
    const a = new Object3D();
    a.name = 'A';
    const b = new Object3D();
    b.name = 'B';
    const ea = world.spawn({ object3d: a });
    const eb = world.spawn({ object3d: b });
    const both = new AnimationClip('Both', 1, [
      new VectorKeyframeTrack('A.position', [0, 1], [0, 0, 0, 0, 1, 0]),
      new VectorKeyframeTrack('B.position', [0, 1], [0, 0, 0, 0, 3, 0]),
    ]);
    const animation = new AnimationService(() => {});
    animation.reset(world, [both]);
    expect(animation.play(ea, 'Both')).toBe(true);
    animation.update(0.5);
    expect(a.position.y).toBeCloseTo(0.5, 5);
    expect(b.position.y).toBe(0); // B's track was filtered out of A's copy.
    expect(animation.isPlaying(eb)).toBe(false);
    animation.stop(ea, { fade: 0.2 });
    expect(animation.current(ea)).toBeNull();
  });

  it("finds Blender's suffixed copies (Idle.001) by the plain name, preferring an exact match", () => {
    const world = new World();
    const hero = world.spawn({ object3d: Object.assign(new Object3D(), { name: 'Hero' }) });
    const villain = world.spawn({ object3d: Object.assign(new Object3D(), { name: 'Villain' }) });
    const animation = new AnimationService(() => {});
    animation.reset(world, [rise('Hero', 'Idle'), rise('Villain', 'Idle.001'), slide('Villain', 'Idle')]);
    expect(animation.clips(villain)).toEqual(['Idle', 'Idle.001']);
    expect(animation.play(hero, 'Idle')).toBe(true);
    expect(animation.current(hero)).toBe('Idle');
    const onlyCopy = new AnimationService(() => {});
    onlyCopy.reset(world, [rise('Hero', 'Idle'), rise('Villain', 'Idle.001')]);
    expect(onlyCopy.clips(villain)).toEqual(['Idle.001']);
    expect(onlyCopy.play(villain, 'Idle')).toBe(true);
    expect(onlyCopy.current(villain)).toBe('Idle.001');
    expect(onlyCopy.isPlaying(villain, 'Idle')).toBe(true);
    expect(onlyCopy.isPlaying(villain, 'Idle.001')).toBe(true);
    onlyCopy.update(0.5);
    expect(villain.object3d.position.y).toBeCloseTo(1, 5);
    // An Animator naming the plain clip still takes live edits.
    villain.add(Animator, { clip: 'Idle', playOnStart: true });
    onlyCopy.update(0);
    villain.get(Animator).speed = 2;
    onlyCopy.update(0.1); // 0.5 s + 0.1 s at double speed
    expect(villain.object3d.position.y).toBeCloseTo(1.4, 5);
  });

  it('warns once when a clip does not animate the entity', () => {
    const { e, animation, warnings } = setup([rise('Elsewhere', 'Wave'), rise('Box')]);
    expect(animation.play(e, 'Wave')).toBe(false);
    expect(animation.play(e, 'Wave')).toBe(false);
    expect(animation.play(e, 'Nope')).toBe(false);
    expect(warnings).toEqual([
      '[animation] Clip "Wave" does not animate "Box" or its children. Clips for it: Rise',
      '[animation] Clip "Nope" does not animate "Box" or its children. Clips for it: Rise',
    ]);
  });

  it('animates bones of a skinned mesh under the entity (an armature)', () => {
    const world = new World();
    const rig = new Group();
    rig.name = 'Rig';
    const root = new Bone();
    root.name = 'Root';
    const tip = new Bone();
    tip.name = 'Tip';
    tip.position.y = 1;
    root.add(tip);
    const skin = new SkinnedMesh(new BufferGeometry());
    skin.name = 'Skin';
    rig.add(root, skin);
    skin.bind(new Skeleton([root, tip]));
    const entity = world.spawn({ object3d: rig, components: { Animator: { clip: 'Wave' } } });
    const turned = new Quaternion().setFromAxisAngle({ x: 1, y: 0, z: 0 } as never, 0.8);
    const wave = new AnimationClip('Wave', 1, [
      new QuaternionKeyframeTrack('Tip.quaternion', [0, 1], [0, 0, 0, 1, ...turned.toArray()]),
    ]);
    const animation = new AnimationService(() => {});
    animation.reset(world, [wave]);
    animation.update(0);
    animation.update(1 - 1e-6);
    expect(animation.isPlaying(entity, 'Wave')).toBe(true);
    expect(tip.quaternion.angleTo(turned)).toBeLessThan(1e-3);
  });

  it('forgets mixers of destroyed entities and clears on reset', () => {
    const { world, e, animation } = setup([rise('Box')], { clip: 'Rise' });
    animation.update(0);
    e.destroy();
    animation.update(0.1);
    expect(animation.current(e)).toBeNull();
    const again = world.spawn({ object3d: Object.assign(new Mesh(), { name: 'Box' }), components: { Animator: { clip: 'Rise' } } });
    animation.reset(world, []);
    animation.update(0);
    expect(animation.isPlaying(again)).toBe(false);
  });
});
