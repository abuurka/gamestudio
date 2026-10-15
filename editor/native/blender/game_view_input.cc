// SPDX-License-Identifier: GPL-3.0-or-later
//
// Game View input vocabulary: Blender event types to the DOM names the surface protocol uses
// (docs/fork/SURFACE.md section 4.3). No Blender structures cross the link, only these strings.

#include "WM_types.hh"

#include "gs_gameview/gameview.hh"

#include "game_view.hh"

namespace blender {

bool game_view_dom_key(const int event_type, GameViewDomKey *r_key)
{
  static const char *letter_codes[26] = {
      "KeyA", "KeyB", "KeyC", "KeyD", "KeyE", "KeyF", "KeyG", "KeyH", "KeyI",
      "KeyJ", "KeyK", "KeyL", "KeyM", "KeyN", "KeyO", "KeyP", "KeyQ", "KeyR",
      "KeyS", "KeyT", "KeyU", "KeyV", "KeyW", "KeyX", "KeyY", "KeyZ"};
  static const char *letter_keys[26] = {"a", "b", "c", "d", "e", "f", "g", "h", "i",
                                        "j", "k", "l", "m", "n", "o", "p", "q", "r",
                                        "s", "t", "u", "v", "w", "x", "y", "z"};
  static const char *letter_shift_keys[26] = {"A", "B", "C", "D", "E", "F", "G", "H", "I",
                                              "J", "K", "L", "M", "N", "O", "P", "Q", "R",
                                              "S", "T", "U", "V", "W", "X", "Y", "Z"};
  static const char *digit_codes[10] = {"Digit0",
                                        "Digit1",
                                        "Digit2",
                                        "Digit3",
                                        "Digit4",
                                        "Digit5",
                                        "Digit6",
                                        "Digit7",
                                        "Digit8",
                                        "Digit9"};
  static const char *numpad_codes[10] = {"Numpad0",
                                         "Numpad1",
                                         "Numpad2",
                                         "Numpad3",
                                         "Numpad4",
                                         "Numpad5",
                                         "Numpad6",
                                         "Numpad7",
                                         "Numpad8",
                                         "Numpad9"};
  static const char *digit_keys[10] = {"0", "1", "2", "3", "4", "5", "6", "7", "8", "9"};
  static const char *function_codes[24] = {"F1",  "F2",  "F3",  "F4",  "F5",  "F6",
                                            "F7",  "F8",  "F9",  "F10", "F11", "F12",
                                            "F13", "F14", "F15", "F16", "F17", "F18",
                                            "F19", "F20", "F21", "F22", "F23", "F24"};

  const auto set = [r_key](const char *code, const char *key, const char *shift_key = nullptr) {
    r_key->code = code;
    r_key->key = key;
    r_key->shift_key = shift_key;
    return true;
  };

  if (event_type >= EVT_AKEY && event_type <= EVT_ZKEY) {
    const int i = event_type - EVT_AKEY;
    return set(letter_codes[i], letter_keys[i], letter_shift_keys[i]);
  }
  if (event_type >= EVT_ZEROKEY && event_type <= EVT_NINEKEY) {
    const int i = event_type - EVT_ZEROKEY;
    return set(digit_codes[i], digit_keys[i]);
  }
  if (event_type >= EVT_PAD0 && event_type <= EVT_PAD9) {
    const int i = event_type - EVT_PAD0;
    return set(numpad_codes[i], digit_keys[i]);
  }
  if (event_type >= EVT_F1KEY && event_type <= EVT_F24KEY) {
    const int i = event_type - EVT_F1KEY;
    return set(function_codes[i], function_codes[i]);
  }

  switch (event_type) {
    /* Modifiers. */
    case EVT_LEFTSHIFTKEY:
      return set("ShiftLeft", "Shift");
    case EVT_RIGHTSHIFTKEY:
      return set("ShiftRight", "Shift");
    case EVT_LEFTCTRLKEY:
      return set("ControlLeft", "Control");
    case EVT_RIGHTCTRLKEY:
      return set("ControlRight", "Control");
    case EVT_LEFTALTKEY:
      return set("AltLeft", "Alt");
    case EVT_RIGHTALTKEY:
      return set("AltRight", "Alt");
    case EVT_OSKEY:
      return set("MetaLeft", "Meta");
    case EVT_CAPSLOCKKEY:
      return set("CapsLock", "CapsLock");

    /* Editing and whitespace. */
    case EVT_ESCKEY:
      return set("Escape", "Escape");
    case EVT_TABKEY:
      return set("Tab", "Tab");
    case EVT_RETKEY:
      return set("Enter", "Enter");
    case EVT_SPACEKEY:
      return set("Space", " ");
    case EVT_BACKSPACEKEY:
      return set("Backspace", "Backspace");
    case EVT_DELKEY:
      return set("Delete", "Delete");
    case EVT_INSERTKEY:
      return set("Insert", "Insert");

    /* Navigation. */
    case EVT_LEFTARROWKEY:
      return set("ArrowLeft", "ArrowLeft");
    case EVT_RIGHTARROWKEY:
      return set("ArrowRight", "ArrowRight");
    case EVT_UPARROWKEY:
      return set("ArrowUp", "ArrowUp");
    case EVT_DOWNARROWKEY:
      return set("ArrowDown", "ArrowDown");
    case EVT_HOMEKEY:
      return set("Home", "Home");
    case EVT_ENDKEY:
      return set("End", "End");
    case EVT_PAGEUPKEY:
      return set("PageUp", "PageUp");
    case EVT_PAGEDOWNKEY:
      return set("PageDown", "PageDown");

    /* Punctuation (US positions; `key` comes from the typed text when there is any). */
    case EVT_SEMICOLONKEY:
      return set("Semicolon", ";");
    case EVT_PERIODKEY:
      return set("Period", ".");
    case EVT_COMMAKEY:
      return set("Comma", ",");
    case EVT_QUOTEKEY:
      return set("Quote", "'");
    case EVT_ACCENTGRAVEKEY:
      return set("Backquote", "`");
    case EVT_MINUSKEY:
      return set("Minus", "-");
    case EVT_PLUSKEY:
      return set("Equal", "+");
    case EVT_EQUALKEY:
      return set("Equal", "=");
    case EVT_SLASHKEY:
      return set("Slash", "/");
    case EVT_BACKSLASHKEY:
      return set("Backslash", "\\");
    case EVT_LEFTBRACKETKEY:
      return set("BracketLeft", "[");
    case EVT_RIGHTBRACKETKEY:
      return set("BracketRight", "]");
    case EVT_GRLESSKEY:
      return set("IntlBackslash", "<");

    /* Numeric keypad. */
    case EVT_PADASTERKEY:
      return set("NumpadMultiply", "*");
    case EVT_PADSLASHKEY:
      return set("NumpadDivide", "/");
    case EVT_PADMINUS:
      return set("NumpadSubtract", "-");
    case EVT_PADPLUSKEY:
      return set("NumpadAdd", "+");
    case EVT_PADENTER:
      return set("NumpadEnter", "Enter");
    case EVT_PADPERIOD:
      return set("NumpadDecimal", ".");

    /* System and media. */
    case EVT_PAUSEKEY:
      return set("Pause", "Pause");
    case EVT_APPKEY:
      return set("ContextMenu", "ContextMenu");
    case EVT_MEDIAPLAY:
      return set("MediaPlayPause", "MediaPlayPause");
    case EVT_MEDIASTOP:
      return set("MediaStop", "MediaStop");
    case EVT_MEDIAFIRST:
      return set("MediaTrackPrevious", "MediaTrackPrevious");
    case EVT_MEDIALAST:
      return set("MediaTrackNext", "MediaTrackNext");

    default:
      return false;
  }
}

uint32_t game_view_dom_modifiers(const int event_modifier)
{
  uint32_t bits = 0;
  if (event_modifier & KM_SHIFT) {
    bits |= gs::gameview::kShift;
  }
  if (event_modifier & KM_CTRL) {
    bits |= gs::gameview::kControl;
  }
  if (event_modifier & KM_ALT) {
    bits |= gs::gameview::kAlt;
  }
  if (event_modifier & KM_OSKEY) {
    bits |= gs::gameview::kMeta;
  }
  return bits;
}

int game_view_dom_button(const int event_type)
{
  switch (event_type) {
    case LEFTMOUSE:
      return 0;
    case MIDDLEMOUSE:
      return 1;
    case RIGHTMOUSE:
      return 2;
    case BUTTON4MOUSE:
      return 3;
    case BUTTON5MOUSE:
      return 4;
    default:
      return -1;
  }
}

int game_view_dom_buttons_bit(const int dom_button)
{
  /* `MouseEvent.buttons` orders middle and right the other way round from `button`. */
  switch (dom_button) {
    case 0:
      return 1;
    case 1:
      return 4;
    case 2:
      return 2;
    case 3:
      return 8;
    case 4:
      return 16;
    default:
      return 0;
  }
}

}  // namespace blender
