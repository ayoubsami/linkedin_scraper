from pathlib import Path

path = Path("profiles_to_scrape.txt")  # or replace with your file path

lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()

seen = set()
out = []
for line in lines:
    key = line.strip()  # change to `key = line` if you only want exact duplicates
    if not key:
        continue  # skip empty/whitespace-only lines
    if key in seen:
        continue
    seen.add(key)
    out.append(line)

path.write_text("\n".join(out) + "\n", encoding="utf-8")
print(f"Done. Kept {len(out)} unique lines in {path}")