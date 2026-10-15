import { AudioContext as ThreeAudioContext, Object3D, PerspectiveCamera, PositionalAudio } from 'three';
import { afterEach, describe, expect, it } from 'vitest';
import { AUDIO_SETTINGS_KEY, AudioService, type AudioSettingsStore, type Sound } from '../src/audio/audio';
import { World } from '../src/ecs/world';
import { replaceComponents } from '../src/game/edits';
import { SaveStore } from '../src/game/save';
import { AudioSource } from '../src/schema';
import { FakeGain, fakeAudio, type FakeAudioContext, type FakeBufferSource, type FakeNode } from './helpers';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

/** A sound's Three.js Audio, whose `source` is the buffer source playing now. */
function audioOf(sound: Sound): { gain: FakeGain; source: FakeBufferSource; isPlaying: boolean } {
  return (sound as unknown as { audio: { gain: FakeGain; source: FakeBufferSource; isPlaying: boolean } }).audio;
}

/** The gain from a sound's own gain node to the speakers: the product of the gains on its (single) path. */
function gainToSpeakers(sound: Sound, context: FakeAudioContext): number {
  let gain = 1;
  let node: FakeNode | undefined = audioOf(sound).gain;
  for (let hops = 0; node && node !== context.destination && hops < 8; hops++) {
    if (node instanceof FakeGain) gain *= node.gain.value;
    expect(node.outputs.size).toBe(1);
    node = [...node.outputs][0];
  }
  expect(node).toBe(context.destination);
  return Math.round(gain * 1e6) / 1e6;
}

function memoryStore(initial: Record<string, unknown> = {}): AudioSettingsStore & { data: Map<string, unknown>; writes: number } {
  const data = new Map(Object.entries(initial));
  const store = {
    data,
    writes: 0,
    get: (key: string) => data.get(key),
    set: (key: string, value: unknown) => {
      store.writes++;
      data.set(key, JSON.parse(JSON.stringify(value)));
    },
  };
  return store;
}

function setup() {
  const fake = fakeAudio();
  const warnings: string[] = [];
  const audio = new AudioService(fake.env, (m) => warnings.push(m));
  const world = new World();
  audio.reset(world, (path) => `/__gs/export/files/${path}`);
  const camera = new PerspectiveCamera();
  world.root.add(camera);
  return { fake, audio, world, camera, warnings, context: () => fake.contexts[0] as FakeAudioContext };
}

afterEach(() => ThreeAudioContext.setContext(undefined as never));

describe('AudioSource and the autoplay rules', () => {
  it('waits for the first user input, then starts autoplay sounds on a listener riding the camera', async () => {
    const { fake, audio, world, camera } = setup();
    const music = world.spawn({ name: 'Music', components: { AudioSource: { clip: 'sfx/music.ogg', loop: true, autoplay: true, spatial: false } } });
    audio.update(camera);
    expect(audio.unlocked).toBe(false);
    expect(fake.contexts).toHaveLength(0); // No AudioContext before a gesture (no browser warning).
    expect(audio.sounds.map((s) => [s.clip, s.state])).toEqual([['sfx/music.ogg', 'pending']]);
    expect(audio.isPlaying(music)).toBe(false);
    // One-shots before the first input are skipped; looping sounds wait.
    expect(audio.play('sfx/click.ogg').state).toBe('stopped');
    expect(audio.play('sfx/hum.ogg', { loop: true }).state).toBe('pending');

    await flush(); // The clip is fetched and decoded meanwhile.
    expect(fake.loads).toEqual(['/__gs/export/files/sfx/music.ogg', '/__gs/export/files/sfx/hum.ogg']);
    fake.gesture();
    await flush();
    expect(audio.unlocked).toBe(true);
    expect(audio.isPlaying(music)).toBe(true);
    expect(audio.isPlaying('sfx/hum.ogg')).toBe(true);
    const [source] = fake.contexts[0]!.sources;
    expect(source).toMatchObject({ started: true, loop: true });

    audio.update(camera);
    expect(audio.listener?.parent).toBe(camera);
    const other = new PerspectiveCamera();
    audio.update(other); // Follows camera switches.
    expect(audio.listener?.parent).toBe(other);
  });

  it('plays positional sounds that follow their entity, and keeps one-shots going when it is destroyed', async () => {
    const { fake, audio, world, camera } = setup();
    fake.gesture();
    await flush();
    const object = new Object3D();
    object.position.set(3, 0, 0);
    const engine = world.spawn({ object3d: object, components: { AudioSource: { clip: 'sfx/engine.ogg', loop: true, refDistance: 4 } } });
    const bell = world.spawn({ components: { AudioSource: { clip: 'sfx/bell.ogg' } } });
    const hum = audio.play(engine);
    const ding = audio.play(bell);
    expect(audio.play(engine)).toBe(hum); // One copy of a looping source.
    await flush();
    expect(hum.playing && ding.playing).toBe(true);
    const positional = audio.sounds.map((s) => (s as unknown as { audio: unknown }).audio).find((a) => a instanceof PositionalAudio) as PositionalAudio;
    expect(positional.getRefDistance()).toBe(4);
    object.position.set(5, 1, 0);
    audio.update(camera);
    expect(positional.position.toArray()).toEqual([5, 1, 0]);

    engine.destroy();
    bell.destroy();
    expect(hum.state).toBe('stopped'); // Loops stop with their entity...
    expect(ding.state).toBe('playing'); // ...one-shots finish where it was.
    fake.contexts[0]!.sources[1]!.finish();
    expect(ding.state).toBe('stopped');
    expect(audio.sounds).toEqual([]);
  });

  it('applies live edits: volume, clip, autoplay', async () => {
    const { fake, audio, world, camera } = setup();
    fake.gesture();
    await flush();
    const e = world.spawn({ components: { AudioSource: { clip: 'a.ogg', autoplay: true, loop: true, spatial: false } } });
    audio.update(camera);
    await flush();
    const first = audio.sounds[0]!;
    expect(first.playing).toBe(true);

    replaceComponents(e, { AudioSource: { clip: 'a.ogg', autoplay: true, loop: true, spatial: false, volume: 0.25 } });
    audio.update(camera);
    expect(first.volume).toBe(0.25);
    const gain = (first as unknown as { audio: { gain: { gain: { value: number } } } }).audio.gain.gain.value;
    expect(gain).toBe(0.25);

    e.get(AudioSource).clip = 'b.ogg';
    audio.update(camera);
    await flush();
    expect(first.state).toBe('stopped');
    expect(audio.sounds.map((s) => [s.clip, s.state])).toEqual([['b.ogg', 'playing']]);

    e.get(AudioSource).autoplay = false;
    audio.update(camera);
    expect(audio.isPlaying(e)).toBe(false);
    e.get(AudioSource).autoplay = true;
    audio.update(camera);
    await flush();
    expect(audio.isPlaying(e)).toBe(true);
    replaceComponents(e, {});
    expect(audio.isPlaying(e)).toBe(false);
  });

  it('stops, mutes, pauses and resets', async () => {
    const { fake, audio, world } = setup();
    fake.gesture();
    await flush();
    audio.play('x.ogg', { loop: true });
    const y = audio.play('y.ogg', { at: [1, 2, 3], volume: 0.5 });
    await flush();
    expect(audio.sounds).toHaveLength(2);
    audio.stop('x.ogg');
    expect(audio.isPlaying('x.ogg')).toBe(false);
    expect(y.playing).toBe(true);
    audio.volume = 0.5;
    audio.muted = true;
    expect(audio.listener!.gain.gain.value).toBe(0);
    audio.muted = false;
    expect(audio.listener!.gain.gain.value).toBe(0.5);
    audio.setPaused(true);
    expect(fake.contexts[0]!.state).toBe('running'); // Pausing holds sfx sounds; the context runs on (music).
    audio.setPaused(false);
    audio.reset(world, (p) => p);
    expect(audio.sounds).toEqual([]);
  });

  it('reports clips that fail to load, and entities without an AudioSource', async () => {
    const { fake, audio, world, warnings } = setup();
    fake.gesture();
    await flush();
    const sound = audio.play('missing.ogg');
    await flush();
    expect(sound.state).toBe('stopped');
    const plain = world.spawn({ name: 'Plain' });
    expect(audio.play(plain).state).toBe('stopped');
    expect(audio.play('').state).toBe('stopped');
    expect(warnings).toEqual([
      '[audio] Could not load "missing.ogg" (/__gs/export/files/missing.ogg): HTTP 404',
      '[audio] "Plain" has no AudioSource; pass a clip path to ctx.audio.play instead',
      '[audio] ctx.audio.play needs an entity or a clip path; got an empty string',
    ]);
  });
});

describe('channels, persistence and pause', () => {
  it('mixes master × channel × sound volume, with AudioSource.channel and play({ channel })', async () => {
    const { fake, audio, world, camera, context } = setup();
    fake.gesture();
    await flush();
    world.spawn({ name: 'Music', components: { AudioSource: { clip: 'theme.ogg', loop: true, autoplay: true, spatial: false, channel: 'music', volume: 0.5 } } });
    audio.update(camera);
    const coin = audio.play('coin.ogg', { volume: 0.8 });
    const click = audio.play('click.ogg', { channel: 'ui' });
    const hum = audio.play('hum.ogg', { at: [1, 0, 0], loop: true, channel: 'music' });
    await flush();
    const music = audio.sounds.find((s) => s.clip === 'theme.ogg')!;
    expect([music, coin, click, hum].map((s) => [s.channel, s.state])).toEqual([
      ['music', 'playing'],
      ['sfx', 'playing'],
      ['ui', 'playing'],
      ['music', 'playing'],
    ]);
    expect(gainToSpeakers(music, context())).toBe(0.5);

    audio.volume = 0.5; // master
    audio.channel('music').volume = 0.4;
    audio.channel('sfx').volume = 0.25;
    expect(audio.channel('master').volume).toBe(0.5);
    expect(audio.channel('music')).toBe(audio.channel('music')); // One live object per channel.
    expect(gainToSpeakers(music, context())).toBe(0.1); // 0.5 × 0.4 × 0.5
    expect(gainToSpeakers(hum, context())).toBe(0.2); // Positional sounds route the same way.
    expect(gainToSpeakers(coin, context())).toBe(0.1); // 0.5 × 0.25 × 0.8
    expect(gainToSpeakers(click, context())).toBe(0.5);

    audio.channel('sfx').muted = true;
    expect(gainToSpeakers(coin, context())).toBe(0);
    expect(gainToSpeakers(music, context())).toBe(0.1);
    audio.channel('sfx').muted = false;
    audio.channel('master').muted = true;
    expect(audio.muted).toBe(true);
    expect(gainToSpeakers(click, context())).toBe(0);
    audio.channel('master').muted = false;
    audio.channel('ui').volume = -1; // Clamped.
    expect(audio.channel('ui').volume).toBe(0);
    audio.channel('ui').volume = Number.NaN; // Ignored.
    expect(audio.channel('ui').volume).toBe(0);
  });

  it('saves channel settings under gs.audio on change and restores them before the first sound', async () => {
    const store = memoryStore();
    const first = setup();
    first.audio.useSettings(store);
    expect(store.writes).toBe(0); // Loading writes nothing.
    first.audio.channel('music').volume = 0.3;
    first.audio.channel('sfx').muted = true;
    first.audio.volume = 0.8;
    first.audio.volume = 0.8; // Unchanged: not saved again.
    expect(store.writes).toBe(3);
    expect(store.data.get(AUDIO_SETTINGS_KEY)).toEqual({
      master: { volume: 0.8, muted: false },
      music: { volume: 0.3, muted: false },
      sfx: { volume: 1, muted: true },
      ui: { volume: 1, muted: false },
    });

    // The next run restores them, before any sound (the context is made later, with the levels applied).
    const { fake, audio, context } = setup();
    audio.useSettings(store);
    expect([audio.volume, audio.channel('music').volume, audio.channel('sfx').muted]).toEqual([0.8, 0.3, true]);
    expect(fake.contexts).toHaveLength(0);
    fake.gesture();
    await flush();
    const theme = audio.play('theme.ogg', { channel: 'music', loop: true });
    await flush();
    expect(gainToSpeakers(theme, context())).toBe(0.24);

    // Bad or partial saves fall back to the defaults per value.
    audio.useSettings(memoryStore({ [AUDIO_SETTINGS_KEY]: { music: { volume: 'loud', muted: 1 }, sfx: { volume: 0.5 } } }));
    expect([audio.volume, audio.channel('music').volume, audio.channel('music').muted, audio.channel('sfx').volume]).toEqual([1, 1, false, 0.5]);
    expect(gainToSpeakers(theme, context())).toBe(1);
  });

  it('starts at the defaults under the test clock (ctx.save is a store of its own)', () => {
    const save = new SaveStore('audio-test');
    save.isolate(true); // Stands in for the player's storage.
    const { audio } = setup();
    audio.useSettings(save);
    audio.channel('music').volume = 0.2;
    audio.muted = true;
    expect(save.get(AUDIO_SETTINGS_KEY)).toMatchObject({ music: { volume: 0.2 }, master: { muted: true } });
    // What the game does when a playtest begins: an empty store, settings loaded again.
    save.isolate(true);
    audio.useSettings(save);
    expect([audio.volume, audio.muted, audio.channel('music').volume, audio.channel('music').muted]).toEqual([1, false, 1, false]);
    expect(save.has(AUDIO_SETTINGS_KEY)).toBe(false);
  });

  it('pausing holds sfx where they are and resumes them; music plays on, ui always plays', async () => {
    const { fake, audio, context } = setup();
    fake.gesture();
    await flush();
    const music = audio.play('theme.ogg', { loop: true, channel: 'music' });
    const engine = audio.play('engine.ogg', { loop: true });
    const boom = audio.play('boom.ogg', { rate: 2 });
    await flush();
    const musicSource = audioOf(music).source;
    const engineSource = audioOf(engine).source;
    const boomSource = audioOf(boom).source;
    const ctx = context();
    ctx.currentTime = 0.375;

    audio.setPaused(true);
    expect(ctx.state).toBe('running'); // No context suspend: music keeps its place.
    expect(musicSource.stopped).toBe(false);
    expect(audioOf(music).isPlaying).toBe(true);
    expect([engineSource.stopped, boomSource.stopped]).toEqual([true, true]);
    expect([engine.state, boom.state]).toEqual(['playing', 'playing']); // Still the game's sounds, just held.
    boomSource.finish(); // A paused source's late 'ended' does not end the held sound.
    expect(boom.state).toBe('playing');

    // While paused: new sfx wait, ui sounds play at once.
    const late = audio.play('late.ogg');
    const click = audio.play('click.ogg', { channel: 'ui' });
    await flush();
    expect(audioOf(late).isPlaying).toBe(false);
    expect(audioOf(click).isPlaying).toBe(true);
    expect(audioOf(click).source.started).toBe(true);

    ctx.currentTime = 9;
    audio.setPaused(false);
    expect(audioOf(music).source).toBe(musicSource); // Never restarted.
    expect(audioOf(engine).source).not.toBe(engineSource);
    expect(audioOf(engine).source.offset).toBeCloseTo(0.375);
    expect(audioOf(boom).source.offset).toBeCloseTo(0.75); // 0.375 s at double speed.
    expect(audioOf(late).source.started).toBe(true);
    expect(audioOf(late).source.offset).toBe(0);
    expect([engine.playing, boom.playing, late.playing]).toEqual([true, true, true]);
    audioOf(boom).source.finish();
    expect(boom.state).toBe('stopped');
  });

  it('re-routes a source whose channel changes, holding or releasing it under pause', async () => {
    const { fake, audio, world, camera, context } = setup();
    fake.gesture();
    await flush();
    const e = world.spawn({ components: { AudioSource: { clip: 'loop.ogg', loop: true, autoplay: true, spatial: false } } });
    audio.update(camera);
    await flush();
    const sound = audio.sounds[0]!;
    audio.channel('music').volume = 0.5;
    audio.setPaused(true);
    expect(audioOf(sound).isPlaying).toBe(false);
    e.get(AudioSource).channel = 'music';
    audio.update(camera);
    expect(sound.channel).toBe('music');
    expect(audioOf(sound).isPlaying).toBe(true); // Music plays under pause.
    expect(gainToSpeakers(sound, context())).toBe(0.5);
    e.get(AudioSource).channel = 'sfx';
    audio.update(camera);
    expect(audioOf(sound).isPlaying).toBe(false);
    expect(gainToSpeakers(sound, context())).toBe(1);
  });

  it('stops everything on stop and reset; a ui click finishes across a scene change', async () => {
    const { fake, audio, world, warnings } = setup();
    fake.gesture();
    await flush();
    audio.play('theme.ogg', { loop: true, channel: 'music' });
    audio.play('boom.ogg');
    audio.play('menu-loop.ogg', { loop: true, channel: 'ui' });
    const click = audio.play('click.ogg', { channel: 'ui' });
    await flush();
    audio.setPaused(true);
    audio.reset(world, (p) => p);
    expect(audio.sounds).toEqual([click]);
    audio.stop();
    expect(audio.sounds).toEqual([]);

    expect(audio.play('x.ogg', { channel: 'voice' as never }).channel).toBe('sfx');
    expect(audio.channel('voice' as never).volume).toBe(1);
    expect(warnings).toEqual([
      '[audio] Unknown channel "voice": sounds play on sfx, music or ui (using sfx)',
      '[audio] ctx.audio.channel("voice"): the channels are master, music, sfx and ui',
    ]);
  });
});
