local wezterm = require 'wezterm'
return {
  font_size = 14,
  initial_cols = 160,
  initial_rows = 46,
  color_scheme = 'Builtin Solarized Dark',
  enable_wayland = false,
  front_end = 'OpenGL',
  max_fps = 30,
  default_cursor_style = 'SteadyBar',
  enable_tab_bar = false,
  exit_behavior = 'Close',
  window_close_confirmation = 'NeverPrompt',
  check_for_updates = false,
  keys = {
    { key = 'LeftArrow', mods = 'ALT', action = wezterm.action.ActivatePaneDirection 'Left' },
    { key = 'RightArrow', mods = 'ALT', action = wezterm.action.ActivatePaneDirection 'Right' },
    { key = 'UpArrow', mods = 'ALT', action = wezterm.action.ActivatePaneDirection 'Up' },
    { key = 'DownArrow', mods = 'ALT', action = wezterm.action.ActivatePaneDirection 'Down' },
  },
}
