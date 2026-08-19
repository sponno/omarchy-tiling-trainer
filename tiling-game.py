#!/usr/bin/env python3
"""
Omarchy Tiling Trainer — an interactive game for learning Hyprland/Omarchy
window management. Run it in a terminal; it watches the REAL window manager
via `hyprctl` and ticks off missions as you perform them with the real keys.

Controls inside the game:  h = show/hide hint   n = skip level   q = quit
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
        aw = hypr("activewindow") or {}
        self.active = aw.get("address")
        foc = next((m for m in self.monitors if m.get("focused")), self.monitors[0] if self.monitors else {})
        self.ws = foc.get("activeWorkspace", {}).get("id")
        self.special_visible = bool(foc.get("specialWorkspace", {}).get("id"))
        self.by_addr = {cl["address"]: cl for cl in self.clients}
    def on_ws(self, ws):  return [cl for cl in self.clients if cl["workspace"]["id"] == ws]
    def get(self, addr):  return self.by_addr.get(addr)

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
        return any(st.get(a) and st.get(a)["workspace"]["id"] < 0 for a in ctx["spawned"])
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

    # 12 -----------------------------------------------------------------
    def cleaned(st):
        return not others(st) and not any(st.get(a) for a in ctx["spawned"])
    L.append(Level("Clean up",
        f"Close every window you opened: the ones here, the one on workspace {T},\n"
        "and the one in the scratchpad (SUPER+S to show it, focus it, close it).\n"
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
    results = []
    for i, lv in enumerate(levels):
        st = State()
        ctx["warn"] = ""
        if lv.setup: lv.setup(st)
        t0 = time.time(); hint = show_hints; done = False; skipped = False
        while True:
            k = read_key()
            if k == "q": return results, True
            if k == "n": skipped = True; break
            if k == "h": hint = not hint
            st = State()
            if lv.check(st): done = True; break
            cols, rows = shutil.get_terminal_size((100, 30))
            el = time.time() - t0
            head = [
                c(f" {title} ", "white", True) + DIM + f"  level {i+1}/{len(levels)}   {el:5.1f}s   " + RESET
                + DIM + "h=hint  n=skip  q=quit" + RESET,
                "",
                c(f"▶ {lv.title}", "yellow", True),
            ] + ["  " + s for s in lv.story.split("\n")] + [""]
            if hint:
                head += ["  " + s for s in ("Hint: " + lv.hint).split("\n")]
            else:
                head += [DIM + "  (press h for a hint)" + RESET]
            if ctx.get("warn"): head += ["", c("  ⚠ " + ctx["warn"], "red", True)]
            head += ["", DIM + f"  Live map of workspace {ctx['arena']}:" + RESET]
            mh = max(8, rows - len(head) - 3)
            mw = min(cols - 4, 90)
            body = ["  " + s for s in render_map(st, ctx["arena"], ctx["me"], mw, mh)]
            draw(head + body)
            time.sleep(0.12)
        el = time.time() - t0
        results.append((lv.title, el, skipped))
        if done:
            draw([c(f"  ✔ {lv.title} — {el:.1f}s", "green", True), ""])
            time.sleep(0.9)
    return results, False

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
    ctx = {"me": me, "arena": arena, "trip_ws": trip_ws, "spawned": set(), "sizes": {}, "pos": {}}
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
