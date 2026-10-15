// Sound effects. Importing with ?url lets Vite serve them in development and copy them into builds.
import coin from '../../assets/sfx/coin.wav?url';
import crash from '../../assets/sfx/crash.wav?url';
import jump from '../../assets/sfx/jump.wav?url'; // the starter's sounds: jump, lose, whoosh
import lose from '../../assets/sfx/lose.wav?url';
import powerup from '../../assets/sfx/powerup.wav?url';
import smash from '../../assets/sfx/smash.wav?url';
import whoosh from '../../assets/sfx/whoosh.wav?url';

export const sfx = { coin, crash, jump, lose, powerup, smash, whoosh };
