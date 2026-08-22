#!/usr/bin/env python3
"""
Omarchy Tiling Trainer — an interactive game for learning Hyprland/Omarchy
window management. Run it in a terminal; it watches the REAL window manager
via `hyprctl` and ticks off missions as you perform them with the real keys.

Controls inside the game:  h = show/hide hint   n = skip level   b = back a level   q = quit
"""
import json, os, select, shutil, subprocess, sys, termios, time, tty

# ---------------------------------------------------------------- ANSI helpers
CSI = "\x1b["
RESET, BOLD, DIM = CSI+"0m", CSI+"1m", CSI+"2m"
FG = {"red":31,"green":32,"yellow":33,"blue":34,"magenta":35,"cyan":36,"white":37,"grey":90}
def c(txt, col, bold=False):
    return f"{CSI}{'1;' if bold else ''}{FG[col]}m{txt}{RESET}"
def key(k):   # render a key combo like a keycap
    return f"{CSI}1;30;47m {k} {RESET}"
def keys(*ks): return " ".join(key(k) for k in ks)

# ---------------------------------------------------------------- hyprctl
def hypr(*args):
    out = subprocess.run(["hyprctl", "-j", *args], capture_output=True, text=True).stdout
    try: return json.loads(out)
    except json.JSONDecodeError: return None

def dispatch(lua):
    """Omarchy's Hyprland is Lua-configured; dispatchers are Lua calls."""
    subprocess.run(["hyprctl", "dispatch", lua], capture_output=True)

class State:
    def __init__(self):
        self.clients  = [cl for cl in (hypr("clients") or []) if cl.get("mapped", True)]
        self.monitors = hypr("monitors") or []
        self.workspaces = hypr("workspaces") or []
        aw = hypr("activewindow") or {}
        self.active = aw.get("address")
        foc = next((m for m in self.monitors if m.get("focused")), self.monitors[0] if self.monitors else {})
        self.ws = foc.get("activeWorkspace", {}).get("id")
        self.special_visible = bool(foc.get("specialWorkspace", {}).get("id"))
        self.by_addr = {cl["address"]: cl for cl in self.clients}
    def on_ws(self, ws):  return [cl for cl in self.clients if cl["workspace"]["id"] == ws]
    def get(self, addr):  return self.by_addr.get(addr)
    def layout_of(self, ws):
        return next((w.get("tiledLayout") for w in self.workspaces if w.get("id") == ws), None)

def find_self_address():
    """Walk up our parent processes until we hit one that owns a Hyprland window.
    Retries for a few seconds because the terminal may not be mapped yet."""
    deadline = time.time() + 6
    while time.time() < deadline:
        pids = {}
        for cl in (hypr("clients") or []): pids.setdefault(cl["pid"], []).append(cl["address"])
        active = (hypr("activewindow") or {}).get("address")
        pid = os.getpid()
        for _ in range(12):
            if pid in pids:   # ghostty is single-instance: several windows may share a pid
                return active if active in pids[pid] else pids[pid][-1]
            try:
                with open(f"/proc/{pid}/stat") as f:
                    pid = int(f.read().rsplit(")",1)[1].split()[1])
            except Exception: break
            if pid <= 1: break
        time.sleep(0.2)
    aw = hypr("activewindow") or {}
    return aw.get("address")

# ---------------------------------------------------------------- mini-map
def render_map(st, arena, me, width, height):
    """Draw the arena workspace as ASCII boxes."""
    mecl = st.get(me)
    mon = None
    if mecl:
        mon = next((m for m in st.monitors if m["id"] == mecl.get("monitor")), None)
    if mon is None and st.monitors: mon = st.monitors[0]
    if not mon: return ["(no monitor?)"]
    sc = mon.get("scale", 1) or 1
    mx, my, mw, mh = mon["x"], mon["y"], mon["width"]/sc, mon["height"]/sc
    W, H = max(20, width), max(8, height)
    grid = [[" "]*W for _ in range(H)]
    col  = [[None]*W for _ in range(H)]
    wins = st.on_ws(arena)
    # draw tiled first, floating on top, fullscreen last
    order = sorted(wins, key=lambda w: (bool(w.get("fullscreen")), w.get("floating", False)))
    labels = {}
    n = 0
    for w in sorted(wins, key=lambda w: (w["address"] != me, w["address"])):
        if w["address"] == me: labels[w["address"]] = "YOU (tutorial)"
        else:
            n += 1; labels[w["address"]] = f"#{n} {w.get('class','')[:14]}"
    for w in order:
        if w.get("fullscreen"):
            x0,y0,x1,y1 = 0,0,W-1,H-1
        else:
            ax, ay = w["at"]; sw, sh = w["size"]
            x0 = int((ax-mx)/mw*W); x1 = int((ax-mx+sw)/mw*W)-1
            y0 = int((ay-my)/mh*H); y1 = int((ay-my+sh)/mh*H)-1
            x0,x1 = max(0,min(W-1,x0)), max(0,min(W-1,x1))
            y0,y1 = max(0,min(H-1,y0)), max(0,min(H-1,y1))
            if x1-x0 < 3: x1 = min(W-1, x0+3)
            if y1-y0 < 2: y1 = min(H-1, y0+2)
        focused = w["address"] == st.active
        colour = "green" if focused else ("magenta" if w.get("floating") else "grey")
        if w["address"] == me and not focused: colour = "cyan"
        fill = "░" if w.get("floating") else " "
        for y in range(y0, y1+1):
            for x in range(x0, x1+1):
                edge_y = y in (y0, y1); edge_x = x in (x0, x1)
                if edge_y and edge_x: ch = "┏┓┗┛"[(y==y1)*2 + (x==x1)]
                elif edge_y: ch = "━"
                elif edge_x: ch = "┃"
                else: ch = fill
                grid[y][x] = ch; col[y][x] = colour
        lab = labels.get(w["address"], "?")
        tags = []
        if focused: tags.append("focused")
        if w.get("floating"): tags.append("floating")
        if w.get("fullscreen"): tags.append("FULLSCREEN")
        if w.get("pinned"): tags.append("pinned")
        if len(w.get("grouped") or []) >= 2: tags.append("grouped")
        text = (lab + ("  ["+", ".join(tags)+"]" if tags else ""))[:max(0, x1-x0-1)]
        ly = y0 + 1 if y1 - y0 >= 2 else y0
        for i, ch in enumerate(text):
            if x0+1+i < x1: grid[ly][x0+1+i] = ch; col[ly][x0+1+i] = colour
    lines = []
    for y in range(H):
        s, cur = "", None
        for x in range(W):
            cc = col[y][x]
            if cc != cur:
                s += RESET + (f"{CSI}{'1;' if cc=='green' else ''}{FG[cc]}m" if cc else "")
                cur = cc
            s += grid[y][x]
        lines.append(s + RESET)
    return lines

# ---------------------------------------------------------------- levels
class Level:
    def __init__(self, title, story, hint, check, setup=None, boss=True):
        self.title, self.story, self.hint, self.check, self.setup, self.boss = title, story, hint, check, setup, boss

def build_levels(ctx):
    me, arena = ctx["me"], ctx["arena"]
    trip = ctx["trip_ws"]
    A, T = str(arena), str(trip)
    L = []

    def others(st): return [w for w in st.on_ws(arena) if w["address"] != me]
    def tiled(st):  return [w for w in st.on_ws(arena) if not w.get("floating") and not w.get("fullscreen")]
    def track(st):
        for w in others(st): ctx["spawned"].add(w["address"])

    # 1 ------------------------------------------------------------------
    L.append(Level("Spawn a tile",
        "Omarchy tiles windows for you: nothing overlaps, nothing needs dragging.\n"
        "Every new window SPLITS the focused window's space in half (this is the\n"
        "'dwindle' layout). Open a new terminal and watch the map split.",
        f"{keys('SUPER','RETURN')} opens a terminal.  ({keys('SUPER','SHIFT','RETURN')} = browser)",
        lambda st: (track(st), len(others(st)) >= 1)[1]))

    # 2 ------------------------------------------------------------------
    L.append(Level("Move focus away",
        "The GREEN box on the map is the focused window. In a tiling WM you move\n"
        "focus with the keyboard, in a direction. Focus the new terminal.",
        f"{keys('SUPER','←/→/↑/↓')} focuses the window in that direction.  {keys('ALT','TAB')} cycles.",
        lambda st: st.active in {w['address'] for w in others(st)}))

    L.append(Level("...and come back",
        "Now bring focus back to this tutorial window.",
        f"{keys('SUPER','←/→/↑/↓')} toward the YOU box (or {keys('ALT','TAB')}).",
        lambda st: st.active == me))

    # 3 ------------------------------------------------------------------
    L.append(Level("Three's a crowd",
        "Open ONE more terminal. Notice it splits whichever window is focused —\n"
        "and dwindle alternates the split direction as windows get smaller.",
        f"{keys('SUPER','RETURN')} again.",
        lambda st: (track(st), len(others(st)) >= 2)[1]))

    # 3b ------------------------------------------------------------------
    def cycle_setup(st): ctx["cseen"] = set()
    def cycle_check(st):
        here = {w["address"] for w in st.on_ws(arena)}
        ctx["cseen"] = (ctx["cseen"] & here) | ({st.active} if st.active in here else set())
        ctx["note"] = f"visited {len(ctx['cseen'])}/{max(3, len(here))} windows"
        return len(ctx["cseen"]) >= max(3, len(here))
    L.append(Level("Do a lap",
        "Directions are great when you can see the target — but ALT+TAB just\n"
        "cycles to the next window, no thinking required. Do a full lap:\n"
        "visit every window on this workspace.",
        f"{keys('ALT','TAB')} focuses the next window ({keys('SHIFT','ALT','TAB')} = previous).\n"
        "  It also raises the window on top if something overlaps it.",
        cycle_check, setup=cycle_setup))

    # 4 ------------------------------------------------------------------
    def snap_sizes(st): ctx["sizes"] = {w["address"]: tuple(w["size"]) for w in tiled(st)}
    def size_changed(st):
        return any(tuple(w["size"]) != ctx["sizes"].get(w["address"]) for w in tiled(st)
                   if w["address"] in ctx["sizes"]) and not any(w.get("floating") for w in st.on_ws(arena))
    L.append(Level("Flip the split",
        "Don't like the direction a window got split? Toggle the split orientation\n"
        "of the focused window (side-by-side <-> stacked).",
        f"{keys('SUPER','J')} toggles the split.",
        size_changed, setup=snap_sizes))

    # 5 ------------------------------------------------------------------
    def snap_pos(st): ctx["pos"] = {w["address"]: tuple(w["at"]) for w in tiled(st)}
    def swapped(st):
        now = {w["address"]: tuple(w["at"]) for w in tiled(st)}
        old = ctx["pos"]
        return any(now.get(a) in old.values() and now.get(a) != old.get(a)
                   for a in now if a in old)
    L.append(Level("Swap places",
        "Windows can be swapped with their neighbours — the layout stays, the\n"
        "windows trade slots. Swap the focused window in some direction.",
        f"{keys('SUPER','SHIFT','←/→/↑/↓')} swaps the focused window that way.",
        swapped, setup=snap_pos))

    # 6 ------------------------------------------------------------------
    def resized(st):
        w = st.get(st.active)
        if not w or w["address"] not in ctx["sizes"]: return False
        ow, oh = ctx["sizes"][w["address"]]; nw, nh = w["size"]
        return (abs(nw-ow) > 15 or abs(nh-oh) > 15) and not w.get("floating")
    L.append(Level("Resize",
        "Tiles can be resized too: shrink/expand the focused window. There are\n"
        "'a little' and 'a lot' variants, or hold SUPER and drag with the RIGHT\n"
        "mouse button.",
        f"{keys('SUPER','MINUS')} / {keys('SUPER','EQUAL')} width   {keys('SUPER','SHIFT','MINUS')} / {keys('SUPER','SHIFT','EQUAL')} height\n"
        f"  add ALT for a little, CTRL for a lot.  Or {keys('SUPER','right-drag')}.",
        resized, setup=snap_sizes))

    # 6b ------------------------------------------------------------------
    WIDTH_DIR = os.path.expanduser("~/.local/state/omarchy/windows")
    def width_setup(st):
        ctx["wphase"], ctx["wt0"], ctx["waddr"], ctx["w0"] = 0, time.time(), None, 0
    def width_check(st):
        if ctx["wphase"] == 0:
            try:
                newest = max((os.path.getmtime(os.path.join(WIDTH_DIR, f))
                              for f in os.listdir(WIDTH_DIR)), default=0)
            except OSError:
                newest = 0
            w = st.get(st.active)
            if newest > ctx["wt0"] and w and not w.get("floating"):
                ctx["wphase"], ctx["waddr"], ctx["w0"] = 1, w["address"], w["size"][0]
                ctx["note"] = "1/3 width saved ✓ — now change the width"
            return False
        w = st.get(ctx["waddr"])
        if not w or w.get("floating"): return False
        if ctx["wphase"] == 1:
            if abs(w["size"][0] - ctx["w0"]) > 15:
                ctx["wphase"] = 2; ctx["note"] = "2/3 resized ✓ — now restore the saved width"
            return False
        return abs(w["size"][0] - ctx["w0"]) <= 15
    L.append(Level("Save & restore width",
        "Found a width you like? Omarchy can remember it. Three steps, all on\n"
        "one tiled window: SAVE its width, mess the width up, then RESTORE it.",
        f"{keys('SUPER','ALT','HOME')} saves.  {keys('SUPER','MINUS')} / {keys('SUPER','EQUAL')} to mess it up.  {keys('SUPER','HOME')} restores.",
        width_check, setup=width_setup))

    # 6c ------------------------------------------------------------------
    def pseudo_check(st):
        w = st.get(st.active)
        if not w or w.get("floating") or w.get("fullscreen"): return False
        old = ctx["sizes"].get(w["address"])
        if not old: return False
        others_same = all(tuple(o["size"]) == ctx["sizes"].get(o["address"], tuple(o["size"]))
                          for o in tiled(st) if o["address"] != w["address"])
        if others_same and (abs(w["size"][0]-old[0]) > 15 or abs(w["size"][1]-old[1]) > 15):
            ctx["paddr"] = w["address"]; return True
        return False
    L.append(Level("Pseudo-size it",
        "A PSEUDO window stays in its tile but shrinks to its natural size —\n"
        "the layout doesn't move, only that window does. Try it on a terminal.\n"
        "(If nothing changes, its natural size already fills the tile — press n.)",
        f"{keys('SUPER','P')} toggles pseudo on the focused window.",
        pseudo_check, setup=snap_sizes, boss=False))
    def pseudo_back(st):
        if not ctx.get("paddr"): return True   # previous level skipped: nothing to undo
        w = st.get(ctx["paddr"])
        old = ctx["sizes"].get(ctx["paddr"])
        if not w or not old: return True       # window gone
        return abs(w["size"][0]-old[0]) <= 15 and abs(w["size"][1]-old[1]) <= 15
    L.append(Level("...and un-pseudo",
        "It's a toggle — fill the tile again.",
        f"{keys('SUPER','P')} again on the same window.",
        pseudo_back, boss=False))

    # 7 ------------------------------------------------------------------
    L.append(Level("Go fullscreen (on me)",
        "Any window can go fullscreen. Make THIS tutorial window fullscreen so\n"
        "you can still read along.",
        f"{keys('SUPER','F')} fullscreen.  ({keys('SUPER','ALT','F')} = full width, {keys('SUPER','CTRL','F')} = fullscreen but keep bar)",
        lambda st: bool((st.get(me) or {}).get("fullscreen"))))

    L.append(Level("...and back to a tile",
        "Fullscreen is a toggle. Put me back in the grid.",
        f"{keys('SUPER','F')} again.",
        lambda st: not (st.get(me) or {}).get("fullscreen") and st.active == me, boss=False))

    # 8 ------------------------------------------------------------------
    L.append(Level("Let one float",
        "Sometimes you want a window to hover OVER the tiles (a calculator, a\n"
        "video). Toggle the focused window to floating. Floating windows can be\n"
        "moved with SUPER + left-drag and resized with SUPER + right-drag.",
        f"{keys('SUPER','T')} toggles floating.  ({keys('SUPER','O')} pops out AND pins across workspaces)",
        lambda st: any(w.get("floating") for w in st.on_ws(arena))))

    L.append(Level("...and tile it again",
        "Toggle it back into the grid.",
        f"{keys('SUPER','T')} on the floating window.",
        lambda st: not any(w.get("floating") for w in st.on_ws(arena)), boss=False))

    # 8b — groups ---------------------------------------------------------
    def grouped_wins(st): return [w for w in st.on_ws(arena) if len(w.get("grouped") or []) >= 2]
    L.append(Level("Group up",
        "Windows can be GROUPED into one tile with tabs — like browser tabs,\n"
        "but for any windows. Turn a terminal into a group, then shove its\n"
        "neighbour in with it.",
        f"{keys('SUPER','G')} makes the focused window a group.  Then focus a neighbour\n"
        f"  and {keys('SUPER','ALT','←/→/↑/↓')} moves it INTO the group in that direction.",
        lambda st: bool(grouped_wins(st))))

    def gcycle_setup(st): ctx["gseen"] = set()
    def gcycle(st):
        w = st.get(st.active)
        if w and len(w.get("grouped") or []) >= 2:
            ctx["gseen"].add(w["address"])
            ctx["note"] = f"visited {len(ctx['gseen'])}/2 tabs"
        return len(ctx["gseen"]) >= 2
    L.append(Level("Ride the tabs",
        "The group shows a tab bar on top. Cycle through it — visit both\n"
        "windows in the group.",
        f"{keys('SUPER','ALT','TAB')} next tab, {keys('SUPER','SHIFT','ALT','TAB')} previous.\n"
        f"  (Also {keys('SUPER','CTRL','←/→')}, or {keys('SUPER','ALT','1..5')} jumps straight to a tab)",
        gcycle, setup=gcycle_setup))

    L.append(Level("Break up the band",
        "Groups dissolve as easily as they form. Ungroup everything on this\n"
        "workspace.",
        f"{keys('SUPER','ALT','G')} pops the focused window out.  Or {keys('SUPER','G')} on the group dissolves it.",
        lambda st: not grouped_wins(st), boss=False))

    # 8c — scrolling layout ------------------------------------------------
    L.append(Level("Shift into scroll",
        "Every workspace has a LAYOUT. So far you've played 'dwindle' (the\n"
        "splitting you know). The other is 'scrolling': windows line up on an\n"
        "endless horizontal strip you scroll along. Switch this workspace.",
        f"{keys('SUPER','L')} toggles the workspace layout.",
        lambda st: st.layout_of(arena) == "scrolling"))

    L.append(Level("...and back to dwindle",
        "Scroll around if you like — focus keys slide the strip. Then bring\n"
        "this workspace back to dwindle.",
        f"{keys('SUPER','L')} again.",
        lambda st: st.layout_of(arena) == "dwindle"))

    # 9 ------------------------------------------------------------------
    def ws_trip(st):
        if st.ws == trip: ctx["visited"] = True
        return ctx.get("visited") and st.ws == arena
    def ws_setup(st): ctx["visited"] = False
    L.append(Level("Take a trip",
        f"Workspaces are virtual desktops, numbered 1-10. We're on workspace {A}.\n"
        f"Go to workspace {T} (it's empty), then come back here to {A}.",
        f"{keys('SUPER',T)} then {keys('SUPER',A)}.   (Also {keys('SUPER','TAB')} next / {keys('SUPER','SHIFT','TAB')} previous)",
        ws_trip, setup=ws_setup))

    # 9b -------------------------------------------------------------------
    def boom_setup(st): ctx["b_visited"] = False
    def boom(st):
        if st.ws != arena: ctx["b_visited"] = True
        return ctx.get("b_visited") and st.ws == arena
    L.append(Level("Boomerang",
        f"Hyprland remembers your FORMER workspace. Hop anywhere (say {T}),\n"
        "then snap straight back without thinking about numbers.",
        f"{keys('SUPER',T)} then {keys('SUPER','CTRL','TAB')} returns to the former workspace.\n"
        f"  (Also {keys('SUPER','scroll-wheel')} rolls through workspaces in order.)",
        boom, setup=boom_setup))

    # 9c -------------------------------------------------------------------
    if len(hypr("monitors") or []) > 1:
        def mon_setup(st):
            foc = next((m for m in st.monitors if m.get("focused")), {})
            ctx["mon0"], ctx["mon_away"] = foc.get("id"), False
        def mon_check(st):
            foc = next((m for m in st.monitors if m.get("focused")), {})
            if foc.get("id") != ctx.get("mon0"): ctx["mon_away"] = True
            return ctx.get("mon_away") and foc.get("id") == ctx.get("mon0")
        L.append(Level("Second screen",
            "You have more than one monitor — focus hops between them too.\n"
            "Visit the other monitor, then come back here.",
            f"{keys('CTRL','ALT','TAB')} focuses the next monitor ({keys('SHIFT','CTRL','ALT','TAB')} previous).\n"
            f"  ({keys('SUPER','SHIFT','ALT','←/→/↑/↓')} moves a whole WORKSPACE between monitors)",
            mon_check, setup=mon_setup))

    # 10 -----------------------------------------------------------------
    def shipped(st):
        moved = [a for a in ctx["spawned"] if st.get(a) and st.get(a)["workspace"]["id"] == trip]
        if st.get(me) and st.get(me)["workspace"]["id"] != arena:
            ctx["warn"] = f"You moved ME! Bring me back: SUPER+SHIFT+{A}"
        else: ctx["warn"] = ""
        return bool(moved) and st.ws == arena
    L.append(Level("Ship a window away",
        f"Move a window to another workspace. Focus one of the OTHER terminals\n"
        f"(not me!) and send it to workspace {T}. You'll follow it — then come\n"
        f"back here with SUPER+{A}.",
        f"{keys('SUPER','←/→')} to focus a terminal, {keys('SUPER','SHIFT',T)} to move it, {keys('SUPER',A)} to return.\n"
        f"  ({keys('SUPER','SHIFT','ALT',T)} moves it WITHOUT following)",
        shipped))

    # 11 -----------------------------------------------------------------
    def stashed(st):
        for a in ctx["spawned"]:
            w = st.get(a)
            if w and w["workspace"]["id"] < 0:
                ctx["stash_addr"] = a; return True
        return False
    L.append(Level("Stash in the scratchpad",
        "The scratchpad is a hidden workspace you can pop over the top of any\n"
        "workspace — great for a music player or notes. Focus a terminal here\n"
        "and send it to the scratchpad.",
        f"{keys('SUPER','ALT','S')} sends focused window to scratchpad.",
        stashed))

    def toggled(st):
        if st.special_visible: ctx["shown"] = True
        return ctx.get("shown") and not st.special_visible
    L.append(Level("...peek and hide",
        "Now toggle the scratchpad: show it, then hide it again.",
        f"{keys('SUPER','S')} toggles the scratchpad (press twice).",
        toggled, setup=lambda st: ctx.__setitem__("shown", False), boss=False))

    def unstash_check(st):
        a = ctx.get("stash_addr")
        w = st.get(a) if a else None
        if w: return w["workspace"]["id"] == arena
        return any(st.get(x) and st.get(x)["workspace"]["id"] == arena for x in ctx["spawned"])
    L.append(Level("...and unstash it",
        "Here's the trap: SUPER+S only HIDES the scratchpad — the window still\n"
        "LIVES there. To truly bring one back, move it onto a real workspace\n"
        "like any other window. Rescue the stashed terminal: bring it back here.",
        f"{keys('SUPER','S')} to show it, focus it, then {keys('SUPER','SHIFT',A)} moves it back to this workspace.",
        unstash_check))

    # 11b -----------------------------------------------------------------
    def gaps_css():
        return (hypr("getoption", "general:gaps_in") or {}).get("css")
    def gaps_setup(st): ctx["gaps0"], ctx["gaps_flipped"] = gaps_css(), False
    def gaps_check(st):
        if ctx["gaps0"] is None: return True   # can't read the option: don't block
        cur = gaps_css()
        if cur != ctx["gaps0"]:
            ctx["gaps_flipped"] = True; ctx["note"] = "gaps toggled ✓ — now bring them back"
        return ctx["gaps_flipped"] and cur == ctx["gaps0"]
    L.append(Level("Mind the gaps",
        "Looks are toggleable too. Kill the gaps between tiles for maximum\n"
        "screen estate, then bring them back.\n"
        "(Related: SUPER+BACKSPACE toggles window transparency, and\n"
        "SUPER+CTRL+BACKSPACE makes a lone window square.)",
        f"{keys('SUPER','SHIFT','BACKSPACE')} toggles window gaps (press twice).",
        gaps_check, setup=gaps_setup))

    # 12 -----------------------------------------------------------------
    def cleaned(st):
        return not others(st) and not any(st.get(a) for a in ctx["spawned"])
    L.append(Level("Clean up",
        f"Close every window you opened: the ones here and the one on workspace {T}.\n"
        "(If anything is still stashed: SUPER+S to show it, focus it, close it.)\n"
        "Leave me alive!",
        f"{keys('SUPER','W')} closes the focused window.  ({keys('CTRL','ALT','DELETE')} closes ALL — careful!)",
        cleaned))
    return L

# ---------------------------------------------------------------- UI
def read_key():
    if select.select([sys.stdin], [], [], 0)[0]:
        return sys.stdin.read(1)
    return None

def draw(lines):
    cols, rows = shutil.get_terminal_size((100, 30))
    out = CSI + "H"
    for ln in lines[:rows-1]:
        out += ln + CSI + "K\n"
    out += CSI + "J"
    sys.stdout.write(out); sys.stdout.flush()

def play(levels, ctx, title, show_hints):
    results = [None] * len(levels)
    i = 0
    while i < len(levels):
        lv = levels[i]
        st = State()
        ctx["warn"] = ctx["note"] = ""
        if lv.setup: lv.setup(st)
        t0 = time.time(); hint = show_hints; done = False; skipped = False; back = False
        while True:
            k = read_key()
            if k == "q": return [r for r in results if r], True
            if k == "n": skipped = True; break
            if k == "b" and i > 0: back = True; break
            if k == "h": hint = not hint
            st = State()
            if lv.check(st): done = True; break
            cols, rows = shutil.get_terminal_size((100, 30))
            el = time.time() - t0
            head = [
                c(f" {title} ", "white", True) + DIM + f"  level {i+1}/{len(levels)}   {el:5.1f}s   " + RESET
                + DIM + "h=hint  n=skip  b=back  q=quit" + RESET,
                "",
                c(f"▶ {lv.title}", "yellow", True),
            ] + ["  " + s for s in lv.story.split("\n")] + [""]
            if hint:
                head += ["  " + s for s in ("Hint: " + lv.hint).split("\n")]
            else:
                head += [DIM + "  (press h for a hint)" + RESET]
            if ctx.get("warn"): head += ["", c("  ⚠ " + ctx["warn"], "red", True)]
            if ctx.get("note"): head += ["", c("  ▸ " + ctx["note"], "cyan")]
            head += ["", DIM + f"  Live map of workspace {ctx['arena']}:" + RESET]
            mh = max(8, rows - len(head) - 3)
            mw = min(cols - 4, 90)
            body = ["  " + s for s in render_map(st, ctx["arena"], ctx["me"], mw, mh)]
            draw(head + body)
            time.sleep(0.12)
        if back:
            i -= 1
            continue
        el = time.time() - t0
        results[i] = (lv.title, el, skipped)
        if not done:
            i += 1
            continue
        # Quick done flash — auto-advances, with a dim recap of the keys used.
        draw([c(f"  ✔ {lv.title} — {el:.1f}s", "green", True), ""]
             + ["  " + DIM + "you did: " + RESET + lv.hint.split("\n")[0]])
        time.sleep(1.4)
        i += 1
    return [r for r in results if r], False

def main():
    if not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        sys.exit("This needs to run inside Hyprland (Omarchy).")
    me = find_self_address()
    if not me: sys.exit("Couldn't find my own window in hyprctl clients.")
    st = State()
    arena = st.get(me)["workspace"]["id"]
    used = {w["workspace"]["id"] for w in st.clients}
    # move to an empty workspace if we're sharing one
    if len(st.on_ws(arena)) > 1:
        empty = [n for n in range(1, 10) if n not in used]
        if empty:
            arena = empty[0]
            dispatch(f'hl.dsp.window.move({{ workspace = "{arena}", window = "address:{me}" }})')
            time.sleep(0.4); st = State()
    used.add(arena)
    trip_ws = next((n for n in list(range(arena+1, 10)) + list(range(1, arena)) if n not in used), (arena % 9) + 1)
    ctx = {"me": me, "arena": arena, "trip_ws": trip_ws, "spawned": set(), "sizes": {}, "pos": {},
           "warn": "", "note": ""}
    levels = build_levels(ctx)

    fd = sys.stdin.fileno(); old = termios.tcgetattr(fd)
    sys.stdout.write(CSI + "?25l")  # hide cursor
    try:
        tty.setcbreak(fd)
        draw([
            c("  OMARCHY TILING TRAINER", "cyan", True), "",
            f"  Workspace {arena} is our arena. Keep this window visible — it tells you what",
            "  to do and watches the window manager to see when you've done it.",
            "", "  Don't worry about messing up: everything is real, so you're practicing the",
            "  real thing. You can always press " + key("SUPER K") + " to see every keybinding.",
            "", c("  Press any key to start…", "yellow", True)])
        while read_key() is None: time.sleep(0.05)
        res, quit_ = play(levels, ctx, "TILING TRAINER", show_hints=True)
        boss = []
        if not quit_:
            draw([c("  TUTORIAL COMPLETE!", "green", True), "",
                  "  Now the BOSS ROUND: the same moves, hints hidden (press h if stuck).",
                  "  Try to go fast.", "", c("  Press any key…", "yellow", True)])
            while read_key() is None: time.sleep(0.05)
            ctx["spawned"] = set()
            boss_levels = [l for l in build_levels(ctx) if l.boss]
            boss, quit_ = play(boss_levels, ctx, "BOSS ROUND", show_hints=False)
        lines = [c("  RESULTS", "cyan", True), ""]
        for name, el, sk in res: lines.append(f"  {'skip' if sk else f'{el:5.1f}s':>6}  {name}")
        if boss:
            lines += ["", c("  Boss round", "magenta", True)]
            for name, el, sk in boss: lines.append(f"  {'skip' if sk else f'{el:5.1f}s':>6}  {name}")
            tot = sum(e for _, e, s in boss if not s)
            lines += ["", c(f"  Boss total: {tot:.1f}s", "yellow", True)]
        lines += ["", "  Cheat sheet any time: " + key("SUPER K") + "   Re-run: ~/Work/tiling-game/play",
                  "", DIM + "  Press any key to exit." + RESET]
        draw(lines)
        while read_key() is None: time.sleep(0.05)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        sys.stdout.write(CSI + "?25h" + RESET + "\n")

if __name__ == "__main__":
    main()
