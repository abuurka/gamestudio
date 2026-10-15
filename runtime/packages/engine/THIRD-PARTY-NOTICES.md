# Third-party notices

@gamestudio/engine is MIT-licensed (see LICENSE). Some of its code comes from other authors, under
the licences below. The GameStudio Vite plugin (@gamestudio/devserver) copies this file into every
web build's THIRD-PARTY-NOTICES.md, after the licences of the bundled packages.

## webgl-noise (3D simplex noise)

3D simplex noise by Ian McEwan and Stefan Gustavson (Ashima Arts),
https://github.com/stegu/webgl-noise (first published at https://github.com/ashima/webgl-noise).
Used in the shader library: Dissolve, EnergyShield, GoldenTransmutation, HotMetal, Iridescence and
Petrification (src/shaders/library/).

Copyright (C) 2011 by Ashima Arts (Simplex noise)
Copyright (C) 2011-2016 by Stefan Gustavson (Classic noise and others)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.

## Hash without Sine

The hash functions (hash11, hash12, hash13, hash22, hash33) by David Hoskins,
https://www.shadertoy.com/view/4djSRW (its MIT-licensed revision). Used in the shader library:
CarPaint, EnergyShield, FlagWave, Flow, FoliageSway, Gem, GoldenTransmutation, GrassWind, Hologram,
Ice, InteractGlint, InteriorMapping, Lava, MossOvergrowth, ParallaxOcclusion, PortalVortex,
ScannerPulse, ShorelineFoam, SnowCover, Water and WetSurface (src/shaders/library/; ParallaxOcclusion
and PortalVortex also port hash12 to TypeScript to bake their textures).

Copyright (c) 2014 David Hoskins.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## Public-domain code

No notice is required for these; they are credited with thanks.

- mulberry32, the random number generator of `ctx.random` (src/game/random.ts), by Tommy Ettinger
  (2017), https://gist.github.com/tommyettinger/46a874533244883189143505d203312c, dedicated to the
  public domain (CC0 1.0).
- The mixing constants of MurmurHash3's 32-bit finaliser by Austin Appleby (public domain),
  https://github.com/aappleby/smhasher, in the hash that bakes the Caustics tile
  (src/shaders/library/caustics/impl.ts).
