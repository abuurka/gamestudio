// Built-in component schemas (docs/SPEC.md §6.3). DOM-free so the dev server's
// registry generator and the engine share exactly the same definitions.
// The behaviour lives in src/physics and src/systems.

import { field } from './fields';
import { defineComponent } from './registry';

export const RigidBody = defineComponent('RigidBody', {
  description: 'Simulates the entity with physics. Needs a Collider to collide.',
  category: 'Physics',
  builtin: true,
  fields: {
    type: field.enum(['dynamic', 'fixed', 'kinematic'], 'dynamic', {
      description: 'dynamic: moved by forces; fixed: never moves; kinematic: moved by code',
    }),
    mass: field.float(1, { min: 0, description: 'Mass in kg (dynamic bodies)' }),
    linearDamping: field.float(0, { min: 0 }),
    angularDamping: field.float(0.05, { min: 0 }),
    gravityScale: field.float(1),
    lockRotation: field.bool(false, { description: 'Prevent the body from rotating' }),
    ccd: field.bool(false, { description: 'Continuous collision detection for fast bodies' }),
    lockX: field.bool(false, { description: 'Keep the X position (Blender X) fixed' }),
    lockY: field.bool(false, { description: 'Keep the height (runtime Y = Blender Z) fixed: top-down games' }),
    lockZ: field.bool(false, {
      description: 'Keep the depth (runtime Z = Blender -Y) fixed: side-scrollers. One lock makes a 2D body that turns only about that axis',
    }),
  },
});

export const Collider = defineComponent('Collider', {
  description: 'Collision shape sized from the mesh bounds. Without a RigidBody it is fixed.',
  category: 'Physics',
  builtin: true,
  fields: {
    shape: field.enum(['auto', 'box', 'sphere', 'capsule', 'convex', 'mesh'], 'auto', {
      description: 'auto = box from the mesh bounds; capsule is upright along Y',
    }),
    friction: field.float(0.5, { min: 0 }),
    restitution: field.float(0, { min: 0, max: 1, description: 'Bounciness' }),
    sensor: field.bool(false, { description: 'Detects overlaps without blocking movement' }),
  },
});

export const CharacterController = defineComponent('CharacterController', {
  description: 'Player movement: WASD/arrows/left stick relative to the camera, Space/A to jump.',
  category: 'Gameplay',
  builtin: true,
  fields: {
    speed: field.float(5, { min: 0, description: 'Metres per second' }),
    jumpSpeed: field.float(6, { min: 0, description: 'Upward speed at jump start (m/s)' }),
    gravity: field.float(20, { min: 0, description: 'Downward acceleration (m/s²)' }),
    turnSpeed: field.float(10, { min: 0, description: 'How fast the mesh turns to face movement' }),
    plane: field.enum(['none', 'side', 'top'], 'none', {
      description:
        'none: 3D, relative to the camera; side: side-scroller, left/right only along the screen, the depth stays exactly; ' +
        'top: top-down, up/down/left/right on the screen, also under a camera looking straight down',
    }),
  },
});

export const FollowCamera = defineComponent('FollowCamera', {
  description: 'Smoothly follows the target entity at a world-space offset.',
  category: 'Camera',
  builtin: true,
  fields: {
    target: field.entity({ description: 'Entity to follow' }),
    offset: field.vec3([0, 4, 8], { description: 'World-space offset from the target (Y-up)' }),
    smoothing: field.float(5, { min: 0, description: 'Higher is snappier; 0 snaps instantly' }),
    lookAtOffset: field.vec3([0, 1, 0], { description: 'Point to look at, relative to the target' }),
  },
});

export const SideCamera = defineComponent('SideCamera', {
  description:
    'Side-scroller and top-down camera: follows the target across the screen, never turns and keeps its depth. ' +
    'Dead zone, look-ahead, smoothing and level bounds. Works with orthographic and perspective cameras.',
  category: 'Camera',
  builtin: true,
  fields: {
    target: field.entity({ description: 'Entity to follow' }),
    follow: field.enum(['both', 'horizontal', 'vertical'], 'both', {
      description: 'Screen axes it follows; the others stay where the camera was placed',
    }),
    offset: field.vec2([0, 1], { description: 'Frames target + this (metres right, up on the screen)' }),
    deadZone: field.vec2([1, 2], { min: 0, description: 'Width and height (m) of the box the target moves in without moving the camera' }),
    lookAhead: field.float(2, { min: 0, unit: 'distance', description: 'Metres it looks ahead in the direction the target moves (left/right)' }),
    smoothing: field.float(5, { min: 0, description: 'Higher is snappier; 0 snaps instantly' }),
    bounds: field.entity({ description: 'Object whose bounding box is the level (e.g. a Wire box): the view stays inside it' }),
    boundsMin: field.vec3([0, 0, 0], { description: 'Level bounds when no bounds object (Y-up); an axis where max <= min is open' }),
    boundsMax: field.vec3([0, 0, 0], { description: 'Level bounds when no bounds object (Y-up); an axis where max <= min is open' }),
    pixelSnap: field.bool(false, { description: 'Orthographic: move in whole screen pixels (pixel art)' }),
  },
});

export const Spin = defineComponent('Spin', {
  description: 'Rotates the entity continuously.',
  category: 'Motion',
  builtin: true,
  fields: {
    speed: field.float(1, { description: 'Radians per second' }),
    axis: field.vec3([0, 1, 0], { description: 'Local rotation axis' }),
  },
});

export const Animator = defineComponent('Animator', {
  description: 'Plays an animation clip (a Blender action) on this object and its children.',
  category: 'Animation',
  builtin: true,
  fields: {
    clip: field.string('', { description: 'Action name to play. Empty: the first clip that animates this object' }),
    loop: field.bool(true, { description: 'Repeat the clip; off plays it once and holds the last frame' }),
    speed: field.float(1, { description: 'Playback speed (1 = as authored, negative plays backwards)' }),
    playOnStart: field.bool(true, { description: 'Start playing when the entity appears' }),
    fade: field.float(0.25, { min: 0, unit: 'time', description: 'Crossfade time in seconds when the clip changes' }),
    graph: field.string('', {
      description: 'Animation graph (defineAnimationGraph) that picks the clips instead of clip and loop; speed scales it',
    }),
    state: field.string('', { description: 'The graph state it plays now (runtime)' }),
  },
});

export const Locomotion = defineComponent('Locomotion', {
  description:
    'Plays idle, walk, run, jump and fall clips from how the entity moves, with crossfades. Put it on the ' +
    'armature that carries the clips (with the CharacterController for a player). Empty names are skipped.',
  category: 'Animation',
  builtin: true,
  fields: {
    idle: field.string('Idle', { description: 'Clip while standing still' }),
    walk: field.string('Walk', { description: 'Clip while moving slower than runSpeed' }),
    run: field.string('Run', { description: 'Clip from runSpeed up' }),
    jump: field.string('Jump', { description: 'Clip played once when it leaves the ground upwards' }),
    fall: field.string('Fall', { description: 'Clip while in the air and coming down' }),
    walkSpeed: field.float(0.3, { min: 0, description: 'Horizontal speed (m/s) from which it walks' }),
    runSpeed: field.float(4, { min: 0, description: 'Horizontal speed (m/s) from which it runs' }),
    fade: field.float(0.15, { min: 0, unit: 'time', description: 'Crossfade seconds between clips' }),
    clip: field.string('', { description: 'The clip it plays now (runtime)' }),
  },
});

export const AudioSource = defineComponent('AudioSource', {
  description: 'A sound attached to this object. Browsers start audio after the first click or key press.',
  category: 'Audio',
  builtin: true,
  fields: {
    clip: field.asset(['ogg', 'mp3', 'wav'], { description: 'Sound file (.ogg, .mp3 or .wav), relative to the project' }),
    volume: field.float(1, { min: 0, description: 'Volume multiplier (1 = as recorded)' }),
    loop: field.bool(false, { description: 'Repeat the sound' }),
    autoplay: field.bool(false, { description: 'Start playing when the entity appears' }),
    spatial: field.bool(true, { description: 'Positional 3D sound; off plays at the same volume everywhere' }),
    refDistance: field.float(2, { min: 0, unit: 'distance', description: 'Distance at which the volume starts to fall off' }),
    maxDistance: field.float(50, { min: 0, unit: 'distance', description: 'No further fall-off beyond this distance' }),
    rolloff: field.float(1, { min: 0, description: 'How quickly the volume falls off with distance' }),
    distanceModel: field.enum(['inverse', 'linear', 'exponential'], 'inverse', {
      description: 'Fall-off curve (Web Audio distance model)',
    }),
    channel: field.enum(['sfx', 'music'], 'sfx', {
      description: 'Volume channel: sfx pauses with the game; music keeps playing under menus (ctx.audio.channel)',
    }),
  },
});

export const ParticleEmitter = defineComponent('ParticleEmitter', {
  description:
    'Emits particles (sparkles, dust, smoke, sparks) from this object: a steady rate and/or a burst per cycle. ' +
    'ctx.vfx.play / stop / emit control it from code.',
  category: 'Effects',
  builtin: true,
  fields: {
    playOnStart: field.bool(true, { description: 'Start emitting when the entity appears' }),
    loop: field.bool(true, { description: 'Repeat the cycle; off emits one cycle and stops' }),
    duration: field.float(1, { min: 0.01, unit: 'time', description: 'Seconds per cycle' }),
    rate: field.float(20, { min: 0, description: 'Particles per second during a cycle' }),
    burst: field.int(0, { min: 0, description: 'Particles emitted at once when a cycle starts' }),
    maxParticles: field.int(200, { min: 1, max: 10000, description: 'Most particles alive at once; more wait until some die' }),
    lifetime: field.vec2([0.6, 1.2], { min: 0, description: 'Seconds a particle lives (min, max)' }),
    speed: field.vec2([1, 2], { description: 'Start speed in m/s (min, max)' }),
    direction: field.vec3([0, 1, 0], { description: 'Emission direction in the object’s space (Y-up)' }),
    spread: field.float(0.4, { min: 0, max: Math.PI, unit: 'angle', description: 'Cone half-angle around the direction; 180° emits everywhere' }),
    shape: field.enum(['point', 'sphere', 'box', 'cone'], 'point', {
      description: 'Where particles start: point; inside a sphere (moving outward); inside a box; on a disc (through the cone)',
    }),
    radius: field.float(0.5, { min: 0, unit: 'distance', description: 'Sphere and cone radius' }),
    box: field.vec3([1, 1, 1], { min: 0, description: 'Box size in metres' }),
    gravity: field.float(0, { description: 'Multiplier of the scene gravity; negative rises' }),
    drag: field.float(0, { min: 0, description: 'Slows particles down (per second)' }),
    size: field.vec2([0.2, 0.05], { min: 0, description: 'Size in metres at birth and at death' }),
    colorStart: field.color([1, 1, 1, 1], { description: 'Colour and opacity at birth' }),
    colorEnd: field.color([1, 1, 1, 0], { description: 'Colour and opacity at death' }),
    brightness: field.float(1, { min: 0, description: 'Colour multiplier; above 1 glows with bloom' }),
    texture: field.enum(['dot', 'spark', 'star', 'smoke', 'square'], 'dot', { description: 'Built-in sprite' }),
    image: field.asset(['png', 'jpg', 'jpeg', 'webp'], { description: 'Sprite image instead of the built-in texture' }),
    blending: field.enum(['additive', 'alpha'], 'additive', { description: 'additive glows (sparks, magic); alpha covers (smoke, dust, confetti)' }),
    stretch: field.float(0, { min: 0, description: 'Stretch sprites along their velocity (sparks)' }),
    spin: field.float(0, { unit: 'angle', description: 'Sprite rotation speed per second (random direction)' }),
    space: field.enum(['world', 'local'], 'world', { description: 'world: particles stay where they were emitted; local: they move with the object' }),
    seed: field.int(0, { description: 'Random seed; emitters with the same seed repeat the same pattern' }),
  },
});

export const PostEffects = defineComponent('PostEffects', {
  description:
    'Screen effects for this scene: bloom (glow), vignette and simple colour grading. Put it on any one object, ' +
    'such as the camera. Everything is off until you turn it on.',
  category: 'Rendering',
  builtin: true,
  fields: {
    bloom: field.bool(false, { description: 'Bright parts glow' }),
    bloomStrength: field.float(0.6, { min: 0, description: 'How strong the glow is' }),
    bloomThreshold: field.float(0.85, { min: 0, description: 'Brightness from which things glow' }),
    bloomRadius: field.float(0.4, { min: 0, max: 1, description: 'How far the glow spreads' }),
    vignette: field.bool(false, { description: 'Darken the corners' }),
    vignetteAmount: field.float(0.35, { min: 0, max: 1, description: 'How dark the corners get' }),
    exposure: field.float(1, { min: 0, description: 'Overall brightness (tone mapping exposure)' }),
    contrast: field.float(1, { min: 0, description: '1 = unchanged' }),
    saturation: field.float(1, { min: 0, description: '1 = unchanged, 0 = grey' }),
    tint: field.color([1, 1, 1, 1], { description: 'Colour multiplier; white = unchanged' }),
  },
});

export const NavMesh = defineComponent('NavMesh', {
  description:
    'Builds a navigation mesh for NavAgents and ctx.nav when the level loads. Put it on any one object (the level ' +
    'root or the floor). It walks on the solid fixed colliders, or only on the meshes under this object.',
  category: 'Navigation',
  builtin: true,
  fields: {
    agentRadius: field.float(0.4, { min: 0, unit: 'distance', description: 'How far paths keep from walls: the widest agent’s radius' }),
    agentHeight: field.float(1.8, { min: 0.1, unit: 'distance', description: 'Headroom an agent needs' }),
    maxClimb: field.float(0.35, { min: 0, unit: 'distance', description: 'Highest step an agent walks up' }),
    maxSlope: field.float(0.785398, { min: 0, max: 1.55, unit: 'angle', description: 'Steepest walkable slope' }),
    cellSize: field.float(0.2, { min: 0.05, unit: 'distance', description: 'Detail: smaller is more exact and slower to build' }),
    source: field.enum(['colliders', 'meshes'], 'colliders', {
      description: 'colliders: every solid fixed collider in the level; meshes: only the meshes of this object and its children',
    }),
  },
});

export const NavAgent = defineComponent('NavAgent', {
  description:
    'Walks to its target along the navigation mesh, around walls and props, with a kinematic character mover. ' +
    'Needs a kinematic RigidBody and a Collider (capsule). The level needs a NavMesh.',
  category: 'Navigation',
  builtin: true,
  fields: {
    target: field.entity({ description: 'Entity to walk to (chase); repaths as it moves' }),
    destination: field.vec3([0, 0, 0], { description: 'Point to walk to when there is no target and useDestination is on (Y-up)' }),
    useDestination: field.bool(false, { description: 'Walk to destination (ctx.nav.moveTo turns it on)' }),
    speed: field.float(3.5, { min: 0, description: 'Metres per second' }),
    acceleration: field.float(20, { min: 0, description: 'm/s²; 0 changes speed instantly' }),
    stoppingDistance: field.float(0.3, { min: 0, unit: 'distance', description: 'Stops this far from the goal' }),
    turnSpeed: field.float(10, { min: 0, description: 'rad/s it turns toward where it walks; 0 leaves turning to you' }),
    autoRepath: field.float(0.5, { min: 0, unit: 'time', description: 'Seconds between new paths to a moving target; 0: only when the goal changes' }),
    separation: field.float(0, { min: 0, unit: 'distance', description: 'Keeps this far from other agents (0: off)' }),
    remaining: field.float(-1, { description: 'Metres of path left (runtime; -1: no path)' }),
  },
});

// Game UI (docs/GAME-UI.md §2.3): data for canvases authored in ui/*.blend. The UI layer reads them;
// no ECS entities are made from canvas elements.

const SOUND = ['ogg', 'mp3', 'wav'] as const;

export const UiCanvas = defineComponent('UiCanvas', {
  description:
    "Makes this camera's scene (in ui/*.blend) a game UI screen: a menu or a HUD, when it opens and how it scales.",
  category: 'UI',
  builtin: true,
  fields: {
    kind: field.enum(['menu', 'hud'], 'menu', {
      description: 'menu: one at a time, takes keyboard and gamepad focus, can pause; hud: stays on screen under menus, mouse only',
    }),
    open: field.enum(['manual', 'start', 'firstStart'], 'manual', {
      description:
        'manual: opened by a button or code; start: whenever the game starts or restarts; ' +
        'firstStart: on the first start after the page loads (title screens)',
    }),
    pause: field.bool(true, { description: 'Pause the game while this menu is open' }),
    pauseMenu: field.bool(false, { description: 'Escape, P or gamepad Start open this menu while playing and close it again' }),
    back: field.enum(['none', 'close', 'resume', 'previous', 'restart'], 'none', {
      description: 'What Escape and gamepad B do while this menu is open; previous reopens the menu this one replaced',
    }),
    scaleMode: field.enum(['fit', 'width', 'height', 'fixed'], 'fit', {
      description: 'fit: the whole design stays visible; width/height: follow that side; fixed: 1 design pixel = 1 CSS pixel',
    }),
    backdrop: field.color([0, 0, 0, 0], { description: 'Drawn over the game behind this menu (alpha: how much it covers)' }),
    layer: field.int(0, { description: 'Order among open screens of the same kind; higher draws on top' }),
    focus: field.entity({ description: 'Button selected first (default: the first in navigation order)' }),
  },
});

export const UiButton = defineComponent('UiButton', {
  description:
    'A button: clicked, or chosen with the keyboard or gamepad. Its area covers this element and its children. ' +
    'Every press also calls ctx.ui.on(<event>): target for event buttons, otherwise the object name.',
  category: 'UI',
  builtin: true,
  fields: {
    action: field.enum(['event', 'close', 'resume', 'open', 'previous', 'restart', 'load', 'locale', 'none'], 'event', {
      description:
        'event: only ctx.ui.on; close/resume: close this screen; open: open the screen named in target; ' +
        'previous: back to the menu this one replaced; restart: ctx.restart(); load: ctx.loadScene(target); ' +
        'locale: switch the UI language to target (empty: the next one)',
    }),
    target: field.string('', { description: 'Screen to open, level to load, language code, or the event name' }),
    disabled: field.bool(false, { description: 'Skipped by navigation and ignores clicks' }),
    order: field.int(0, { min: 0, description: 'Keyboard and gamepad order (1, 2, 3...); 0: by position' }),
    focusScale: field.float(1.06, { min: 0, description: 'Scale while selected' }),
    sound: field.asset(SOUND, { description: 'Played when pressed' }),
    focusSound: field.asset(SOUND, { description: 'Played when selected' }),
  },
});

export const UiAnchor = defineComponent('UiAnchor', {
  description:
    'Where a top-level UI element stays on screens of other shapes: the point of the screen it keeps its distance to, ' +
    'and whether it stretches.',
  category: 'UI',
  builtin: true,
  fields: {
    anchor: field.enum(
      ['auto', 'center', 'top-left', 'top', 'top-right', 'left', 'right', 'bottom-left', 'bottom', 'bottom-right'],
      'auto',
      { description: 'auto: from where the element sits (thirds of the screen)' },
    ),
    stretch: field.enum(['none', 'width', 'height', 'both', 'cover'], 'none', {
      description: 'width/height: that side follows the screen; both: both sides; cover: fill the whole screen (backgrounds)',
    }),
  },
});

export const UiText = defineComponent('UiText', {
  description: 'Outline, shadow and shrink-to-fit for a UI text object.',
  category: 'UI',
  builtin: true,
  fields: {
    outline: field.float(0, { min: 0, description: 'Outline width in design pixels' }),
    outlineColor: field.color([0, 0, 0, 1]),
    shadow: field.vec2([0, 0], { description: 'Drop-shadow offset in design pixels (x right, y down); 0, 0: none' }),
    shadowBlur: field.float(0, { min: 0, description: 'Shadow blur in design pixels' }),
    shadowColor: field.color([0, 0, 0, 0.6]),
    fit: field.bool(false, { description: 'Shrink the text until it fits its text box' }),
  },
});

export const UiBar = defineComponent('UiBar', {
  description: 'A bar (health, progress) whose fill grows with a value from ctx.store, ctx.save or ctx.ui.bar.',
  category: 'UI',
  builtin: true,
  fields: {
    value: field.string('', { description: 'Store path it shows, e.g. health or save.best; empty: set by ctx.ui.bar' }),
    max: field.float(1, { min: 0, description: 'The value of a full bar' }),
    fill: field.entity({ description: 'The child that grows' }),
    direction: field.enum(['right', 'left', 'up', 'down'], 'right', { description: 'Which way the fill grows' }),
    smoothing: field.float(0, { min: 0, unit: 'time', description: 'Seconds to ease toward a new value; 0: instant' }),
  },
});

export const UiState = defineComponent('UiState', {
  description: "Art inside a button that only shows in one of the button's states.",
  category: 'UI',
  builtin: true,
  fields: {
    show: field.enum(['normal', 'focused', 'pressed', 'disabled', 'focusedOrPressed'], 'focused', {
      description: 'Visible only while the nearest UiButton above it is in this state',
    }),
  },
});

// Game UI, Phase 2 (docs/GAME-UI.md §10): conditional visibility, interactive values, 9-slice art.

const COMPARE = ['atLeast', 'below', 'equals', 'notEquals'] as const;

export const UiShow = defineComponent('UiShow', {
  description:
    'Shows this element only while a value compares as set, e.g. a heart while lives is at least 2. ' +
    'Values: numbers, booleans (1/0); missing is 0.',
  category: 'UI',
  builtin: true,
  fields: {
    value: field.string('', { description: 'Value path: lives, store.x, save.best, audio.music' }),
    compare: field.enum(COMPARE, 'atLeast', { description: 'How the value compares with than' }),
    than: field.float(1, { description: 'The number it is compared with' }),
  },
});

export const UiSlider = defineComponent('UiSlider', {
  description:
    'A slider: drag it, or select it and press left/right. It shows and writes a value path ' +
    '(audio.music, save.difficulty, ...) between min and max.',
  category: 'UI',
  builtin: true,
  fields: {
    value: field.string('', { description: 'Value path it shows and writes: audio.music, save.x, store.x' }),
    min: field.float(0),
    max: field.float(1),
    step: field.float(0.1, { min: 0, description: 'Change per key press; also what drags snap to (0: smooth)' }),
    fill: field.entity({ description: 'The child that grows with the value (like a UiBar fill); its bounds are the range' }),
    handle: field.entity({ description: 'Optional knob moved along the range' }),
    direction: field.enum(['right', 'left', 'up', 'down'], 'right', { description: 'Which way the value grows' }),
    disabled: field.bool(false),
    order: field.int(0, { min: 0, description: 'Keyboard and gamepad order, as for UiButton' }),
    sound: field.asset(SOUND, { description: 'Played on each change' }),
  },
});

export const UiToggle = defineComponent('UiToggle', {
  description: 'An on/off switch: click or press it to flip a value path (save.subtitles, ...). Shows `on` or `off`.',
  category: 'UI',
  builtin: true,
  fields: {
    value: field.string('', { description: 'Value path it shows and flips' }),
    on: field.entity({ description: 'Child shown while the value is on' }),
    off: field.entity({ description: 'Child shown while the value is off' }),
    disabled: field.bool(false),
    order: field.int(0, { min: 0, description: 'Keyboard and gamepad order, as for UiButton' }),
    sound: field.asset(SOUND, { description: 'Played when flipped' }),
  },
});

export const UiSlice = defineComponent('UiSlice', {
  description:
    '9-slice art: the corners of the image keep their size when the panel is resized or stretched; ' +
    'the edges and the middle stretch.',
  category: 'UI',
  builtin: true,
  fields: {
    left: field.float(16, { min: 0, description: 'Image pixels kept at the left edge' }),
    right: field.float(16, { min: 0 }),
    top: field.float(16, { min: 0 }),
    bottom: field.float(16, { min: 0 }),
  },
});

// Game UI, Phase 3 (docs/GAME-UI.md §11): 3D models inside canvases.

export const UiModel = defineComponent('UiModel', {
  description:
    "Shows this empty's child objects as a 3D model inside the UI (a character, a gem, a trophy), " +
    'lit and framed in its own small view, turning or playing a clip.',
  category: 'UI',
  builtin: true,
  fields: {
    size: field.vec2([320, 320], { min: 1, description: 'The view in design pixels (width, height), centred on the empty' }),
    view: field.enum(['threeQuarter', 'front', 'side', 'top'], 'threeQuarter', { description: 'Which way the camera looks at the model' }),
    zoom: field.float(1, { min: 0.05, description: 'Framing: above 1 is closer' }),
    spin: field.float(0.6, { unit: 'angle', description: 'Turntable speed per second about the up axis (0: still)' }),
    clip: field.string('', { description: 'Animation clip to loop (empty: none)' }),
    light: field.enum(['studio', 'soft', 'flat'], 'studio', { description: 'studio: key, fill and rim; soft: even; flat: unlit look' }),
    background: field.color([0, 0, 0, 0], { description: 'Behind the model (alpha 0: the UI shows through)' }),
  },
});

/** The built-in component handles, for convenience. */
export const builtinComponents = [
  RigidBody,
  Collider,
  CharacterController,
  FollowCamera,
  SideCamera,
  Spin,
  Animator,
  Locomotion,
  AudioSource,
  ParticleEmitter,
  PostEffects,
  NavMesh,
  NavAgent,
  UiCanvas,
  UiButton,
  UiAnchor,
  UiText,
  UiBar,
  UiState,
  UiShow,
  UiSlider,
  UiToggle,
  UiSlice,
  UiModel,
] as const;
