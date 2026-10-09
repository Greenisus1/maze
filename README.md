# maze

the first-person terminal maze, preserved as supplied. Raycast walls, collectible gems, an exit and minimap. No desktop or pip packages: Python3 standard library only. Requires Linux/POSIX interactive terminal, ANSI colors and Unicode half-block characters.

The Store lists this in Games / 3D games. Choose Run in terminal in the optional Store GUI. Install checks Python and syntax; it does not install packages or change system settings.

Run from the downloaded repository:

    python3 maze.py
    python3 maze.py --fast --no-mouse
    python3 maze.py --maze 20x12 --colors 256

WASD move/strafe. Mouse, Q/E or arrows look. M minimap. X or Escape quit. R makes a new maze on the finish screen. Mouse reporting support varies by SSH client/terminal; use Q/E or arrows and --no-mouse when needed. --fast reduces rendering load; Pi frame rate is not yet measured.

Tested on Linux with a real PTY for title, start, movement/minimap, clean X exit and terminal restoration. Maze connectivity checked at minimum/default/maximum dimensions. Raspberry Pi hardware and a real SSH client are not yet tested. No license added or inferred; author's licensing choice remains his.

Version 1.0.1 removes the 120-column and 40-row rendering caps. The view uses the full terminal size. Linux checked; Raspberry Pi hardware untested.
