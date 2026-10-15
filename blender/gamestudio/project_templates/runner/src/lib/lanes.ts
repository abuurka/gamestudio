// The three lanes as runtime x (Blender X): left, middle, right. The track runs along x = 0, and the
// chunk prefabs in tools/build_level.py are built for this width (LANE there).
export const LANE_WIDTH = 2.2;
export const LANES = [-LANE_WIDTH, 0, LANE_WIDTH] as const;
