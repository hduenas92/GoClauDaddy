import sys, os
base = os.path.dirname(os.path.abspath(__file__))
ps1  = os.path.join(base, "claudioui_server.ps1")
css  = os.path.join(base, "new_style_temp.txt")

with open(ps1, encoding="utf-8") as f: content = f.read()
with open(css, encoding="utf-8") as f: new_css = f.read()

s1 = content.index('<style>')
s2 = content.index('</style>') + len('</style>')

pre = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
       '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>')
new_content = content[:s1] + pre + "\n  " + new_css + content[s2:]

with open(ps1, "w", encoding="utf-8") as f: f.write(new_content)
print(f"Done — {s2-s1} → {len(new_css)} chars")
