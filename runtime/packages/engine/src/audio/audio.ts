// Sound playback (`ctx.audio`) and the built-in AudioSource component, on top of
// Three.js Audio/PositionalAudio and one AudioListener that rides on the active camera.
//
// Browsers only let audio start after a user gesture. The AudioContext is created
// on the first click, tap or key press; until then looping and autoplay sounds wait,
// and one-shot sounds are skipped (they would all fire at once later).
//
// Mixer: every sound plays on a channel (`sfx`, `music` or `ui`), a GainNode feeding the
// listener's gain (`master`). Pausing the game pauses `sfx` sounds where they are; `music` and
// `ui` play on. Channel volumes and mutes persist in `ctx.save` under `gs.audio`.

import {
  Audio,
  AudioContext as ThreeAudioContext,
  AudioListener,
  Object3D,
  PositionalAudio,
  Vector3,
  type Camera,
} from 'three';
import { componentName } from '../ecs/entity';
import type { Entity } from '../ecs/entity';
import type { World } from '../ecs/world';
import { isVec3Input, toVector3, type Vec3Input } from '../math';
import { AudioSource } from '../schema/builtins';
import type { ComponentValues } from '../schema/registry';

type AudioSourceValues = ComponentValues<typeof AudioSource>;
export type DistanceModel = AudioSourceValues['distanceModel'];

/** Mixer channels. A sound's gain is master × its channel × its own volume. */
export type AudioChannelName = 'master' | 'music' | 'sfx' | 'ui';
/** The channel a sound plays on: `sfx` pauses with the game, `music` plays on under pause menus, `ui` always plays. */
export type SoundChannel = Exclude<AudioChannelName, 'master'>;

/** A live mixer channel from `ctx.audio.channel(name)`: changes apply at once and are saved. */
export interface AudioChannel {
  readonly name: AudioChannelName;
  /** 0..1+. Default 1. */
  volume: number;
  muted: boolean;
}

export interface SoundOptions {
  /** Volume multiplier. Default: `AudioSource.volume`, else 1. */
  volume?: number;
  /** Default: `AudioSource.loop`, else false. */
  loop?: boolean;
  /** Playback rate: 2 plays twice as fast (an octave higher). Default 1. */
  rate?: number;
  /** Mixer channel. Default: `AudioSource.channel`, else 'sfx'. */
  channel?: SoundChannel;
  /** For clip paths: play positionally, following this entity or at this world point. */
  at?: Entity | Vec3Input;
  /** Distance fall-off of positional sounds. Defaults: `AudioSource` values, else the AudioSource defaults. */
  refDistance?: number;
  maxDistance?: number;
  rolloff?: number;
  distanceModel?: DistanceModel;
}

export type SoundState = 'pending' | 'playing' | 'stopped';

/** A sound started with `ctx.audio.play`. */
export interface Sound {
  /** The clip path or URL. */
  readonly clip: string;
  /** 'pending' while loading or waiting for the first user input. */
  readonly state: SoundState;
  readonly playing: boolean;
  /** The entity it belongs to (AudioSource, or `at: entity`), or null. */
  readonly entity: Entity | null;
  readonly channel: SoundChannel;
  volume: number;
  stop(): void;
}

export interface AudioAPI {
  /**
   * Plays an entity's AudioSource, or a clip by path (project-relative, e.g.
   * `'assets/sfx/coin.ogg'`, or a URL). Clip paths play non-positionally unless `at` is set.
   */
  play(source: Entity | string, options?: SoundOptions): Sound;
  /** Stops one sound, an entity's sounds, every sound of a clip, or (no argument) everything. */
  stop(target?: Entity | Sound | string): void;
  /** True while a sound of the entity (or clip) is audible. */
  isPlaying(target: Entity | string): boolean;
  /** Fetches and decodes a clip ahead of time. */
  preload(clip: string): Promise<void>;
  /** A live mixer channel: `ctx.audio.channel('music').volume = 0.5`. Saved in `ctx.save` on change. */
  channel(name: AudioChannelName): AudioChannel;
  /** Master volume, 0..1+ (`channel('master').volume`). */
  volume: number;
  muted: boolean;
  /** True once the browser allowed audio (after the first user input). */
  readonly unlocked: boolean;
  /** The Three.js AudioListener on the active camera (null until unlocked). */
  readonly listener: AudioListener | null;
  /** Pending and playing sounds. */
  readonly sounds: readonly Sound[];
}

/** Where channel settings persist: `ctx.save`. */
export interface AudioSettingsStore {
  get(key: string): unknown;
  set(key: string, value: unknown): void;
}

/** The `ctx.save` key of the channel settings: `{ master: { volume, muted }, music: …, sfx: …, ui: … }`. */
export const AUDIO_SETTINGS_KEY = 'gs.audio';

const CHANNELS: readonly AudioChannelName[] = ['master', 'music', 'sfx', 'ui'];
const SOUND_CHANNELS: readonly SoundChannel[] = ['music', 'sfx', 'ui'];

/** Browser hooks, replaceable in tests. */
export interface AudioEnvironment {
  createContext(): AudioContext;
  /** Decodes with the live context, or without one before the first gesture. */
  decode(data: ArrayBuffer, context: AudioContext | null): Promise<AudioBuffer>;
  load(url: string): Promise<ArrayBuffer>;
  /** Calls `callback` on the first user gesture; returns a cleanup function. */
  onFirstGesture(callback: () => void): () => void;
}

export function browserAudioEnvironment(): AudioEnvironment {
  let offline: OfflineAudioContext | null = null;
  return {
    createContext: () => {
      const Ctor = window.AudioContext ?? (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      return new Ctor();
    },
    decode: (data, context) => (context ?? (offline ??= new OfflineAudioContext(1, 1, 48000))).decodeAudioData(data),
    load: async (url) => {
      const res = await fetch(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.arrayBuffer();
    },
    onFirstGesture: (callback) => {
      const events = ['pointerdown', 'mousedown', 'keydown', 'touchend'] as const;
      const handler = (event: Event): void => {
        // Escape and modifier-only presses don't count as user activation in browsers.
        if (event instanceof KeyboardEvent && (event.key === 'Escape' || event.repeat)) return;
        cleanup();
        callback();
      };
      const cleanup = (): void => {
        for (const type of events) window.removeEventListener(type, handler, true);
      };
      for (const type of events) window.addEventListener(type, handler, true);
      return cleanup;
    },
  };
}

interface VoiceSettings {
  volume: number;
  loop: boolean;
  rate: number;
  channel: SoundChannel;
  spatial: boolean;
  refDistance: number;
  maxDistance: number;
  rolloff: number;
  distanceModel: DistanceModel;
  /** Entity to follow, or a fixed world point (spatial only). */
  follow: Entity | null;
  point: Vector3 | null;
  /** Wait for the first gesture instead of being skipped while audio is locked. */
  waitForUnlock: boolean;
}

interface BufferEntry {
  buffer: AudioBuffer | null;
  failed: boolean;
  promise: Promise<void>;
}

interface Level {
  volume: number;
  muted: boolean;
}

const DEFAULTS = {
  refDistance: 2,
  maxDistance: 50,
  rolloff: 1,
  distanceModel: 'inverse' as DistanceModel,
};

class Voice implements Sound {
  state: SoundState = 'pending';
  audio: Audio | PositionalAudio | null = null;
  /** Paused with the game (an `sfx` sound): resumes where it was. Still counts as playing. */
  held = false;

  constructor(
    private readonly service: AudioService,
    readonly clip: string,
    readonly url: string,
    readonly entity: Entity | null,
    readonly settings: VoiceSettings,
  ) {}

  get playing(): boolean {
    return this.state === 'playing';
  }

  get channel(): SoundChannel {
    return this.settings.channel;
  }

  get volume(): number {
    return this.settings.volume;
  }

  set volume(value: number) {
    this.settings.volume = Math.max(0, value);
    this.audio?.setVolume(this.settings.volume);
  }

  stop(): void {
    this.service.stopVoice(this);
  }
}

class Channel implements AudioChannel {
  constructor(
    private readonly service: AudioService,
    readonly name: AudioChannelName,
  ) {}

  get volume(): number {
    return this.service.level(this.name).volume;
  }

  set volume(value: number) {
    this.service.setLevel(this.name, { volume: value });
  }

  get muted(): boolean {
    return this.service.level(this.name).muted;
  }

  set muted(value: boolean) {
    this.service.setLevel(this.name, { muted: value });
  }

  toJSON(): Level {
    return { ...this.service.level(this.name) };
  }
}

interface SourceState {
  seen: AudioSourceValues | null;
  /** The component's own sound (autoplay, or a looping play()). */
  voice: Voice | null;
}

export class AudioService implements AudioAPI {
  listener: AudioListener | null = null;
  unlocked = false;

  private context: AudioContext | null = null;
  private world: World | null = null;
  private unsubscribe: (() => void) | null = null;
  private removeGestureListener: (() => void) | null = null;
  private readonly voices = new Set<Voice>();
  private readonly buffers = new Map<string, BufferEntry>();
  private readonly sources = new Map<Entity, SourceState>();
  private readonly warned = new Set<string>();
  private readonly levels = Object.fromEntries(CHANNELS.map((name) => [name, { volume: 1, muted: false }])) as Record<
    AudioChannelName,
    Level
  >;
  private readonly channels = new Map<AudioChannelName, Channel>();
  /** One GainNode per sound channel, into the listener's (master) gain. Made on unlock. */
  private readonly buses = new Map<SoundChannel, GainNode>();
  private settingsStore: AudioSettingsStore | null = null;
  private paused = false;
  private resolveUrl: (clip: string) => string = (clip) => clip;

  constructor(
    private readonly env: AudioEnvironment,
    private readonly warn: (message: string) => void = (m) => console.warn(m),
  ) {
    this.removeGestureListener = env.onFirstGesture(() => this.unlock());
  }

  /**
   * Starts a new scene: stops every sound and forgets the AudioSource states (decoded clips stay
   * cached). Sounds of entities in `keep` (carried into the scene) play on, and so do `ui`
   * one-shots (the click of the button that changed the scene).
   */
  reset(world: World, resolveUrl: (clip: string) => string, keep: ReadonlySet<Entity> = new Set()): void {
    for (const voice of [...this.voices]) {
      const kept = voice.entity ? keep.has(voice.entity) : voice.settings.channel === 'ui' && !voice.settings.loop;
      if (!kept) this.stopVoice(voice);
    }
    this.unsubscribe?.();
    for (const entity of [...this.sources.keys()]) if (!keep.has(entity)) this.sources.delete(entity);
    this.world = world;
    this.resolveUrl = resolveUrl;
    this.unsubscribe = world.onChange((event) => {
      if (event.type === 'destroy') this.entityGone(event.entity);
      else if (event.type === 'remove' && event.component === componentName(AudioSource)) {
        const state = this.sources.get(event.entity);
        if (state?.voice) this.stopVoice(state.voice);
        this.sources.delete(event.entity);
      }
    });
  }

  /** Applies AudioSource changes, moves the listener to `camera` and positions sounds. Once per simulated frame. */
  update(camera: Camera): void {
    if (this.listener && this.listener.parent !== camera) camera.add(this.listener);
    if (this.world) for (const entity of this.world.query(AudioSource)) this.syncSource(entity);
    for (const voice of this.voices) {
      const follow = voice.settings.follow;
      if (voice.audio instanceof PositionalAudio && follow?.alive) follow.object3d.getWorldPosition(voice.audio.position);
    }
  }

  /**
   * The game was paused or resumed: `sfx` sounds pause where they are and resume from there
   * (new ones wait); `music` and `ui` sounds play on.
   */
  setPaused(paused: boolean): void {
    if (paused === this.paused) return;
    this.paused = paused;
    for (const voice of this.voices) this.applyHold(voice);
  }

  /**
   * Keeps the channel settings in `store` (`ctx.save`) under `gs.audio`: loads them now (the
   * defaults, 1 and unmuted, where none are saved) and saves every change. Call it again when the
   * store's contents change (a playtest's own store).
   */
  useSettings(store: AudioSettingsStore | null): void {
    this.settingsStore = store;
    let saved: unknown;
    try {
      saved = store?.get(AUDIO_SETTINGS_KEY);
    } catch {
      saved = undefined;
    }
    for (const name of CHANNELS) {
      const entry = isRecord(saved) && isRecord(saved[name]) ? saved[name] : {};
      const level = this.levels[name];
      level.volume = typeof entry.volume === 'number' && Number.isFinite(entry.volume) ? Math.max(0, entry.volume) : 1;
      level.muted = entry.muted === true;
    }
    this.applyLevels(true);
  }

  dispose(): void {
    this.stop();
    this.unsubscribe?.();
    this.unsubscribe = null;
    this.removeGestureListener?.();
    this.removeGestureListener = null;
    this.listener?.removeFromParent();
    void this.context?.close?.();
  }

  // --- AudioAPI --------------------------------------------------------------

  get volume(): number {
    return this.levels.master.volume;
  }

  set volume(value: number) {
    this.setLevel('master', { volume: value });
  }

  get muted(): boolean {
    return this.levels.master.muted;
  }

  set muted(value: boolean) {
    this.setLevel('master', { muted: value });
  }

  get sounds(): readonly Sound[] {
    return [...this.voices];
  }

  channel(name: AudioChannelName): AudioChannel {
    let channel = this.channels.get(name);
    if (channel) return channel;
    if (!CHANNELS.includes(name)) {
      this.warnOnce(`channel:${String(name)}`, `[audio] ctx.audio.channel("${String(name)}"): the channels are master, music, sfx and ui`);
      return { name, volume: 1, muted: false };
    }
    this.channels.set(name, (channel = new Channel(this, name)));
    return channel;
  }

  play(source: Entity | string, options: SoundOptions = {}): Sound {
    if (typeof source === 'string') return this.playClip(source, options);
    const data = source.alive ? source.tryGet<AudioSourceValues>(AudioSource) : undefined;
    if (!data || !data.clip) {
      this.warnOnce(
        `nosource:${source.id}`,
        data
          ? `[audio] The AudioSource on "${source.name}" has no clip`
          : `[audio] "${source.name}" has no AudioSource; pass a clip path to ctx.audio.play instead`,
      );
      return this.stoppedVoice(data?.clip ?? '', source);
    }
    const state = this.sourceState(source);
    const loop = options.loop ?? data.loop;
    if (loop && state.voice && state.voice.state !== 'stopped') return state.voice; // One copy of a loop.
    const voice = this.startVoice(data.clip, source, {
      ...this.settingsFrom(data, source),
      volume: options.volume ?? data.volume,
      loop,
      rate: options.rate ?? 1,
      channel: this.soundChannel(options.channel ?? data.channel),
      waitForUnlock: loop,
    });
    if (loop) state.voice = voice;
    return voice;
  }

  stop(target?: Entity | Sound | string): void {
    for (const voice of [...this.voices]) {
      if (
        target === undefined ||
        voice === target ||
        (typeof target === 'string' && voice.clip === target) ||
        (typeof target === 'object' && voice.entity === target)
      ) {
        this.stopVoice(voice);
      }
    }
  }

  isPlaying(target: Entity | string): boolean {
    for (const voice of this.voices) {
      if (voice.playing && (typeof target === 'string' ? voice.clip === target : voice.entity === target)) return true;
    }
    return false;
  }

  preload(clip: string): Promise<void> {
    return this.load(this.resolveUrl(clip), clip).promise;
  }

  // --- Internals ---------------------------------------------------------------

  /** @internal */
  stopVoice(voice: Voice): void {
    if (voice.state === 'stopped') return;
    voice.state = 'stopped';
    voice.held = false;
    this.voices.delete(voice);
    const audio = voice.audio;
    if (audio) {
      if (audio.isPlaying) audio.stop();
      this.release(audio);
    }
    for (const state of this.sources.values()) if (state.voice === voice) state.voice = null;
  }

  /** @internal */
  level(name: AudioChannelName): Readonly<Level> {
    return this.levels[name];
  }

  /** @internal Sets a channel's volume and/or mute, applies it and saves the settings. */
  setLevel(name: AudioChannelName, change: Partial<Level>): void {
    const level = this.levels[name];
    const volume = change.volume === undefined || !Number.isFinite(change.volume) ? level.volume : Math.max(0, change.volume);
    const muted = change.muted === undefined ? level.muted : !!change.muted;
    if (volume === level.volume && muted === level.muted) return;
    level.volume = volume;
    level.muted = muted;
    this.applyLevel(name, false);
    this.saveSettings();
  }

  private playClip(clip: string, options: SoundOptions): Sound {
    if (!clip) {
      this.warnOnce('noclip', '[audio] ctx.audio.play needs an entity or a clip path; got an empty string');
      return this.stoppedVoice('', null);
    }
    const at = options.at;
    const point = isVec3Input(at) ? toVector3(at) : null;
    const follow = at && !point ? (at as Entity) : null;
    const loop = options.loop ?? false;
    return this.startVoice(clip, follow, {
      volume: options.volume ?? 1,
      loop,
      rate: options.rate ?? 1,
      channel: this.soundChannel(options.channel),
      spatial: !!at,
      refDistance: options.refDistance ?? DEFAULTS.refDistance,
      maxDistance: options.maxDistance ?? DEFAULTS.maxDistance,
      rolloff: options.rolloff ?? DEFAULTS.rolloff,
      distanceModel: options.distanceModel ?? DEFAULTS.distanceModel,
      follow,
      point,
      waitForUnlock: loop,
    });
  }

  private settingsFrom(data: AudioSourceValues, entity: Entity): VoiceSettings {
    return {
      volume: data.volume,
      loop: data.loop,
      rate: 1,
      channel: this.soundChannel(data.channel),
      spatial: data.spatial,
      refDistance: data.refDistance,
      maxDistance: data.maxDistance,
      rolloff: data.rolloff,
      distanceModel: data.distanceModel,
      follow: entity,
      point: null,
      waitForUnlock: true,
    };
  }

  /** A valid sound channel: `sfx` when unset, with a warning when unknown. */
  private soundChannel(value: unknown): SoundChannel {
    if (value === undefined || value === null || value === '') return 'sfx';
    if (SOUND_CHANNELS.includes(value as SoundChannel)) return value as SoundChannel;
    this.warnOnce(`soundchannel:${String(value)}`, `[audio] Unknown channel "${String(value)}": sounds play on sfx, music or ui (using sfx)`);
    return 'sfx';
  }

  private startVoice(clip: string, entity: Entity | null, settings: VoiceSettings): Voice {
    const url = this.resolveUrl(clip);
    const voice = new Voice(this, clip, url, entity, settings);
    if (!this.unlocked && !settings.waitForUnlock) {
      voice.state = 'stopped'; // A one-shot before the first user input would play late; skip it.
      return voice;
    }
    this.voices.add(voice);
    const entry = this.load(url, clip);
    if (entry.buffer) this.begin(voice);
    else void entry.promise.then(() => this.begin(voice));
    return voice;
  }

  private stoppedVoice(clip: string, entity: Entity | null): Voice {
    const settings: VoiceSettings = {
      ...DEFAULTS,
      volume: 1,
      loop: false,
      rate: 1,
      channel: 'sfx',
      spatial: false,
      follow: null,
      point: null,
      waitForUnlock: false,
    };
    const voice = new Voice(this, clip, clip, entity, settings);
    voice.state = 'stopped';
    return voice;
  }

  /** Starts a pending voice once its buffer is decoded and audio is unlocked (held instead while its channel is paused). */
  private begin(voice: Voice): void {
    if (voice.state !== 'pending' || !this.unlocked || !this.listener) return;
    const entry = this.buffers.get(voice.url);
    if (!entry?.buffer) {
      if (entry?.failed) this.stopVoice(voice);
      return;
    }
    const s = voice.settings;
    const listener = this.listener;
    let audio: Audio | PositionalAudio;
    if (s.spatial) {
      const positional = new PositionalAudio(listener);
      applyDistance(positional, s);
      const world = this.world?.root;
      if (s.follow?.alive) s.follow.object3d.getWorldPosition(positional.position);
      else if (s.point) positional.position.copy(s.point);
      world?.add(positional);
      positional.updateMatrixWorld(true);
      const panner = positional.panner;
      const t = this.context?.currentTime ?? 0;
      // Start at the right spot instead of ramping in from the origin.
      panner.positionX?.setValueAtTime(positional.position.x, t);
      panner.positionY?.setValueAtTime(positional.position.y, t);
      panner.positionZ?.setValueAtTime(positional.position.z, t);
      audio = positional;
    } else {
      audio = new Audio<GainNode>(listener);
    }
    audio.name = `gs_sound:${voice.clip}`;
    audio.setBuffer(entry.buffer);
    audio.setLoop(s.loop);
    audio.setVolume(s.volume);
    audio.setPlaybackRate(s.rate);
    const ended = audio.onEnded.bind(audio);
    audio.onEnded = () => {
      ended();
      if (voice.audio === audio && !audio.loop) this.stopVoice(voice);
    };
    voice.audio = audio;
    voice.state = 'playing';
    this.route(voice);
    if (this.holds(voice)) voice.held = true; // Starts when the game resumes.
    else audio.play();
  }

  /** Connects a voice's gain to its channel's bus (Three.js connects it to the listener). */
  private route(voice: Voice): void {
    const audio = voice.audio;
    const bus = this.buses.get(voice.settings.channel);
    if (!audio || !bus) return;
    audio.gain.disconnect();
    audio.gain.connect(bus);
  }

  private holds(voice: Voice): boolean {
    return this.paused && voice.settings.channel === 'sfx';
  }

  /** Pauses a playing voice that its channel's pause now holds, or resumes a held one where it was. */
  private applyHold(voice: Voice): void {
    const audio = voice.audio;
    if (!audio || voice.state !== 'playing') return;
    const hold = this.holds(voice);
    if (hold && !voice.held) {
      voice.held = true;
      if (audio.isPlaying) audio.pause();
    } else if (!hold && voice.held) {
      voice.held = false;
      if (!audio.isPlaying) audio.play();
    }
  }

  private release(audio: Audio | PositionalAudio): void {
    try {
      audio.disconnect();
    } catch {
      // Never connected (stopped before it started).
    }
    audio.gain.disconnect();
    audio.removeFromParent();
  }

  private load(url: string, clip: string): BufferEntry {
    let entry = this.buffers.get(url);
    if (entry) return entry;
    const created: BufferEntry = { buffer: null, failed: false, promise: Promise.resolve() };
    created.promise = this.env
      .load(url)
      .then((data) => this.env.decode(data, this.context))
      .then(
        (buffer) => {
          created.buffer = buffer;
        },
        (error) => {
          created.failed = true;
          this.warnOnce(`load:${url}`, `[audio] Could not load "${clip}" (${url}): ${error instanceof Error ? error.message : String(error)}`);
          for (const voice of [...this.voices]) if (voice.url === url) this.stopVoice(voice);
        },
      );
    this.buffers.set(url, created);
    entry = created;
    return entry;
  }

  private unlock(): void {
    if (this.context) return;
    let context: AudioContext;
    try {
      context = this.env.createContext();
    } catch (error) {
      this.warnOnce('context', `[audio] Web Audio is not available: ${String(error)}`);
      return;
    }
    this.context = context;
    ThreeAudioContext.setContext(context);
    this.listener = new AudioListener();
    this.listener.name = 'gs_audio_listener';
    for (const name of SOUND_CHANNELS) {
      const bus = context.createGain();
      bus.connect(this.listener.getInput());
      this.buses.set(name, bus);
    }
    this.applyLevels(true);
    const ready = (): void => {
      if (this.unlocked) return;
      this.unlocked = true;
      for (const voice of [...this.voices]) this.begin(voice);
    };
    if (context.state === 'running') ready();
    else context.resume().then(ready, () => {});
  }

  private applyLevels(immediate: boolean): void {
    for (const name of CHANNELS) this.applyLevel(name, immediate);
  }

  /** Sets a channel's gain node: at once, or with a short ramp (no clicks while a slider moves). */
  private applyLevel(name: AudioChannelName, immediate: boolean): void {
    const level = this.levels[name];
    const value = level.muted ? 0 : level.volume;
    const param = name === 'master' ? this.listener?.gain.gain : this.buses.get(name)?.gain;
    if (!param) return;
    if (immediate || !this.context) {
      param.cancelScheduledValues?.(0);
      param.value = value;
    } else {
      param.setTargetAtTime(value, this.context.currentTime, 0.01);
    }
  }

  private saveSettings(): void {
    if (!this.settingsStore) return;
    const value = Object.fromEntries(CHANNELS.map((name) => [name, { ...this.levels[name] }]));
    try {
      this.settingsStore.set(AUDIO_SETTINGS_KEY, value);
    } catch (error) {
      this.warnOnce('save', `[audio] Could not save the channel settings: ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  private sourceState(entity: Entity): SourceState {
    let state = this.sources.get(entity);
    if (!state) this.sources.set(entity, (state = { seen: null, voice: null }));
    return state;
  }

  private syncSource(entity: Entity): void {
    const data = entity.get<AudioSourceValues>(AudioSource);
    const state = this.sourceState(entity);
    const seen = state.seen;
    state.seen = { ...data };
    if (data.clip && data.clip !== seen?.clip) void this.preload(data.clip);
    const start = (): void => {
      state.voice = this.startVoice(data.clip, entity, this.settingsFrom(data, entity));
    };
    if (!seen) {
      if (data.autoplay && data.clip) start();
      return;
    }
    const voice = state.voice && state.voice.state !== 'stopped' ? state.voice : null;
    if (!voice) {
      const turnedOn = data.autoplay !== seen.autoplay || data.clip !== seen.clip;
      if (data.autoplay && data.clip && turnedOn) start();
      return;
    }
    if (data.clip !== seen.clip || data.spatial !== seen.spatial) {
      this.stopVoice(voice);
      if (data.clip) start();
      return;
    }
    if (seen.autoplay && !data.autoplay) {
      this.stopVoice(voice);
      return;
    }
    Object.assign(voice.settings, {
      loop: data.loop,
      refDistance: data.refDistance,
      maxDistance: data.maxDistance,
      rolloff: data.rolloff,
      distanceModel: data.distanceModel,
    });
    if (data.volume !== seen.volume) voice.volume = data.volume;
    if (data.channel !== seen.channel) {
      voice.settings.channel = this.soundChannel(data.channel);
      this.route(voice);
      this.applyHold(voice);
    }
    const audio = voice.audio;
    if (!audio) return;
    if (data.loop !== seen.loop) audio.setLoop(data.loop);
    if (audio instanceof PositionalAudio) applyDistance(audio, voice.settings);
  }

  /** One-shots keep playing where the entity was; loops and component sounds stop. */
  private entityGone(entity: Entity): void {
    for (const voice of [...this.voices]) {
      if (voice.entity !== entity) continue;
      if (voice.settings.loop || voice.state === 'pending') this.stopVoice(voice);
      else voice.settings.follow = null;
    }
    this.sources.delete(entity);
  }

  private warnOnce(key: string, message: string): void {
    if (this.warned.has(key)) return;
    this.warned.add(key);
    this.warn(message);
  }
}

function applyDistance(audio: PositionalAudio, s: VoiceSettings): void {
  audio.setRefDistance(s.refDistance);
  audio.setMaxDistance(s.maxDistance);
  audio.setRolloffFactor(s.rolloff);
  audio.setDistanceModel(s.distanceModel);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
