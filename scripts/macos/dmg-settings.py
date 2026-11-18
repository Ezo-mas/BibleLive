# dmgbuild settings for BibleLive's disk image (run by scripts/macos/build-app.sh):
#
#   dmgbuild -s dmg-settings.py -D app=BibleLive.app -D background=bg.png BibleLive out.dmg
#
# dmgbuild executes this file with `defines` set. It writes the window layout (.DS_Store)
# itself, without Finder or AppleScript, so it works on a headless build machine. The
# positions match dmg-background.swift: a 640 x 400 point window, icons at y = 180 from
# the top. bg@2x.png next to the background is joined into it for Retina screens.
import os.path

app = defines["app"]  # noqa: F821 (provided by dmgbuild)
background = defines["background"]  # noqa: F821

filesystem = "APFS"
format = "ULMO"  # noqa: A001 (dmgbuild's setting name)
files = [app]
symlinks = {"Applications": "/Applications"}

default_view = "icon-view"
show_status_bar = False
show_tab_view = False
show_toolbar = False
show_pathbar = False
show_sidebar = False
window_rect = ((200, 160), (640, 400))
icon_size = 128
text_size = 13
icon_locations = {
    os.path.basename(app): (170, 180),
    "Applications": (470, 180),
}
# No hide_extensions: it sets Finder flags on the app, which `codesign --strict` rejects,
# and Finder hides an app's .app extension by default anyway.
