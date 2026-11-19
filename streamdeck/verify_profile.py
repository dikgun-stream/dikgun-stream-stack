import json
import os
import sys

base = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.environ["TEMP"], "sdtest", "profiles",
    "MRX4M90L-GH25-1C1J-QU4E-HBQZCSII3GQA.sdProfile")

print("checking:", base)
ok = True

manifests = []
for root, _dirs, files in os.walk(base):
    if "manifest.json" in files:
        manifests.append(os.path.join(root, "manifest.json"))

for man in manifests:
    rel = os.path.relpath(man, base)
    try:
        j = json.load(open(man, encoding="utf-8"))
    except Exception as exc:
        print("  !! INVALID JSON:", rel, exc)
        ok = False
        continue
    acts = j.get("Actions") or {}
    bad = []
    for k, a in acts.items():
        p = (a.get("Settings") or {}).get("path")
        if p and not os.path.isfile(p):
            bad.append((k, a.get("Name"), p))
    print("  %-58s %2d buttons, %d broken path(s)" % (rel, len(acts), len(bad)))
    for k, name, p in bad:
        print("      %-5s %-14s %s" % (k, name, p))
        ok = False

# the new button specifically
page2 = None
for man in manifests:
    if "8H3YBY19" in man:
        page2 = json.load(open(man, encoding="utf-8"))
if page2:
    bot = (page2.get("Actions") or {}).get("3,1")
    print()
    print("  BOT button on admin page:", "present" if bot else "MISSING")
    if bot:
        print("    name  :", bot.get("Name"))
        print("    uuid  :", bot.get("UUID"))
        print("    path  :", (bot.get("Settings") or {}).get("path"))
        print("    title :", (bot.get("States") or [{}])[0].get("Title"))

print()
print("RESULT:", "all manifests valid and every path resolves" if ok
      else "problems found - see above")
sys.exit(0 if ok else 1)
