#!/usr/bin/env python3
"""Read and drive TikTok LIVE Studio's go-live state from the command line.

Two ways to start a live, and why this picks the UI:

  * `room.goLive(payload, {liveSource, fastGoLiveType})` - the app's internal
    call. The payload is large (title, hashtags, topic ids, branded-content
    flags, bitrate type...) and version-specific, so synthesising it would be
    fragile and could start a live with wrong settings.
  * the app's own "Go LIVE" button - the app builds all of that itself, so this
    is what we drive: click, wait for the pre-live panel, optionally confirm.

Safety: `start` refuses to press the final button unless --confirm is given, so a
stray deck press opens the panel and waits instead of broadcasting. Every action
prints the live status it observed before and after.

Commands:
    python livestudio_live.py status           # read-only
    python livestudio_live.py panel            # open the pre-live panel
    python livestudio_live.py start --confirm  # panel + confirm + wait for live
    python livestudio_live.py stop             # press End LIVE (needs --confirm too)
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import lsclient as ls

S = ls.SERVICE

# liveStatus enum values observed on this build
STATUS = {0: "default", 4: "offline/idle", 2: "living"}
LIVE_BUTTONS = ("Go LIVE", "Go live", "GO LIVE")
STOP_BUTTONS = ("End LIVE", "End live", "Stop LIVE", "End the LIVE")


def read_status() -> dict:
    body = (f"const st = await {S}.room.liveStatus.get();"
            f"const loc = await {S}.room.localLiveStatus.get();"
            f"const str_ = await {S}.room.streamingStatus.get();"
            f"const u = await {S}.user.anchorInfo.get();"
            f"const r = await {S}.room.roomInfo.get();"
            f"return {{liveStatus: st, localLiveStatus: loc, streamingStatus: str_,"
            f" anchor: u ? u.nickname : null, roomId: r ? r.id_str : null}};")
    r = ls.run(body)
    v = r.get("value") if isinstance(r, dict) else None
    return v if isinstance(v, dict) else {"error": json.dumps(r)[:300]}


def _click_by_text(labels: tuple[str, ...], last: bool = False) -> dict:
    """Dispatch a full pointer sequence on the first (or last) element with that text."""
    js = """(cfg) => {
      const want = cfg.labels;
      const els = [...document.querySelectorAll('button,div,span')];
      const hits = els.filter(e => want.includes((e.innerText||'').replace(/\\s+/g,' ').trim())
                                 && e.getBoundingClientRect().width > 40
                                 && e.getBoundingClientRect().height > 18);
      if (!hits.length) return {clicked:false, tried: want};
      const el = cfg.last ? hits[hits.length-1] : hits[0];
      const r = el.getBoundingClientRect();
      const o = {bubbles:true, cancelable:true, composed:true,
                 clientX:r.left+r.width/2, clientY:r.top+r.height/2, view:window};
      for (const ty of ['pointerover','pointerenter','pointerdown','mousedown','pointerup','mouseup','click']) {
        const C = ty.startsWith('pointer') ? (self.PointerEvent||MouseEvent) : MouseEvent;
        el.dispatchEvent(new C(ty,o));
      }
      return {clicked:true, text:(el.innerText||'').trim().slice(0,24),
              cls:(el.className||'').toString().slice(0,60), count:hits.length};
    }"""
    r = ls.run(f"return (({js})({json.dumps({'labels': list(labels), 'last': last})}))")
    v = r.get("value") if isinstance(r, dict) else None
    return v if isinstance(v, dict) else {"clicked": False, "raw": r}


def cmd_status(_args) -> int:
    st = read_status()
    print("  " + json.dumps(st))
    ls_ = st.get("liveStatus")
    print(f"  interpreted: {STATUS.get(ls_, 'unknown')}")
    return 0


def cmd_panel(_args) -> int:
    st = read_status()
    print(f"  before: {json.dumps(st)}")
    res = _click_by_text(LIVE_BUTTONS)
    print(f"  clicked Go LIVE: {json.dumps(res)}")
    if not res.get("clicked"):
        print("  could not find a Go LIVE button - is the app on the studio screen?")
        return 1
    time.sleep(2.0)
    # report what the panel now offers, without pressing anything
    body = ("const els = [...document.querySelectorAll('button')];"
            "return els.map(e=>(e.innerText||'').trim()).filter(Boolean).slice(0,14);")
    r = ls.run(body)
    print(f"  panel buttons: {json.dumps(r.get('value'))[:300]}")
    return 0


def cmd_start(args) -> int:
    st = read_status()
    print(f"  before: {json.dumps(st)}")
    if STATUS.get(st.get("liveStatus")) == "living" or st.get("streamingStatus") not in (0, None):
        print("  already live or streaming - refusing to start again")
        return 1

    res = _click_by_text(LIVE_BUTTONS)
    print(f"  step 1 open panel: {json.dumps(res)}")
    if not res.get("clicked"):
        return 1
    time.sleep(2.5)

    if not args.confirm:
        print("  panel open. NOT pressing the final button (pass --confirm to broadcast).")
        body = ("const els = [...document.querySelectorAll('button')];"
                "return els.map(e=>(e.innerText||'').trim()).filter(Boolean).slice(0,14);")
        print(f"  panel buttons: {json.dumps(ls.run(body).get('value'))[:300]}")
        return 0

    # the confirm button is the primary one in the panel; try the usual labels
    before = len(ls.run("return document.querySelectorAll('button').length").get("value") or 0)
    for labels in (("Go LIVE", "Go live"), ("Start Live", "Start live", "Start"), ):
        r = _click_by_text(labels, last=True)
        print(f"  step 2 confirm ({labels[0]}): {json.dumps(r)}")
        if r.get("clicked"):
            break
    time.sleep(3.0)

    deadline = time.time() + args.wait
    last = None
    while time.time() < deadline:
        st = read_status()
        cur = (st.get("liveStatus"), st.get("streamingStatus"), st.get("roomId"))
        if cur != last:
            print(f"  waiting: liveStatus={st.get('liveStatus')} streaming={st.get('streamingStatus')} room={st.get('roomId')}")
            last = cur
        if st.get("roomId") or STATUS.get(st.get("liveStatus")) == "living":
            print(f"  LIVE: {json.dumps(st)}")
            return 0
        time.sleep(3.0)
    print(f"  did not reach live within {args.wait}s - last state {json.dumps(read_status())}")
    return 1


def cmd_stop(args) -> int:
    st = read_status()
    print(f"  before: {json.dumps(st)}")
    if not args.confirm:
        print("  refusing to stop without --confirm")
        return 1
    res = _click_by_text(STOP_BUTTONS)
    print(f"  clicked stop: {json.dumps(res)}")
    if not res.get("clicked"):
        return 1
    time.sleep(3.0)
    # often a confirm dialog follows
    for labels in (("End LIVE", "End live", "Stop", "Confirm", "End"),):
        r = _click_by_text(labels, last=True)
        print(f"  confirm: {json.dumps(r)}")
        break
    time.sleep(4.0)
    print(f"  after: {json.dumps(read_status())}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="TikTok LIVE Studio go-live control")
    ap.add_argument("command", choices=["status", "panel", "start", "stop"])
    ap.add_argument("--confirm", action="store_true",
                    help="actually press the broadcast/stop button")
    ap.add_argument("--wait", type=int, default=60, help="seconds to wait for live")
    ap.add_argument("--json", action="store_true", help="machine-readable status only")
    args = ap.parse_args(argv)

    try:
        ls.main_target()
    except Exception as exc:  # noqa: BLE001
        print(f"  LIVE Studio not reachable over CDP: {exc}")
        return 2

    if args.json:
        print(json.dumps(read_status()))
        return 0
    return {"status": cmd_status, "panel": cmd_panel,
            "start": cmd_start, "stop": cmd_stop}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
