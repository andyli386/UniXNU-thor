"""Rebuild the v238 Settings diagnostic from its recorded sources.

base/ builds the v224 library; base/ + settings.patch builds the library v225 to v236 embedded;
about237.patch adds the Ethernet answer and the main-queue watchdog (v237); about238.patch makes the
hang dump print every thread with the exported symbol of each frame.  Every stage is compared byte
for byte with the library that was shipped.
"""
from pathlib import Path
import hashlib, shutil, subprocess, tempfile

p = Path(__file__).resolve().parent
root = p.parent.parent
name = 'libpong-settings-v185.dylib'
sha = lambda blob: hashlib.sha256(blob).hexdigest()
assert sha((p / 'settings.patch').read_bytes()) == '7b058b7f754cc4917535d6401d01fdf71b558e46124d384388bf697647510435'
assert sha((p / 'about237.patch').read_bytes()) == '58af83cc9d293478d2bfd993cede9d0b603e65da91e7df8af04db75c93012828'
with tempfile.TemporaryDirectory() as tmp:
    source = Path(tmp) / 'source'
    shutil.copytree(p / 'base', source)

    def build():
        subprocess.run(['/bin/bash', 'build-settings-trace.sh'], check=True, cwd=source,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return (source / 'runtime' / name).read_bytes()

    def apply(patch):
        subprocess.run(['patch', '--batch', '--fuzz=0', '-s', '-p1', '-i', patch], check=True, cwd=source)

    control = build()
    assert control == (root.parent / 'v224-unified/runtime' / name).read_bytes()
    assert sha(control) == '9a3c7e995ac3cedaa678626210af4982d9f8fc4fbf968432362b53ea6392646a'
    apply(p / 'settings.patch')
    middle = build()
    assert middle == (root.parent / 'v236-unified/runtime' / name).read_bytes()
    assert sha(middle) == 'c7e016950f6e2636e6cd1fcd07fdef80f5f53e153bb146731750f1114f192f18'
    apply(p / 'about237.patch')
    seventh = build()
    assert seventh == (root.parent / 'v237-unified/runtime' / name).read_bytes()
    assert sha(seventh) == '82bb1690d6a763c9b5f4d2957e30d1845d82c7d5c352526c343077f0a47d2917'
    apply(p / 'about238.patch')
    candidate = build()
    assert candidate == (p / name).read_bytes() == (root / 'runtime' / name).read_bytes()
    assert sha(candidate) == 'c2c4970e9e29dc246b6719e157d6fecf76ce33b4a2fa2593fc09719e1146f5be'
    assert len(candidate) <= 128 * 1024 and b'Pong Settings: About v238 ' in candidate
    assert b'Pong Settings: About v238 ' not in seventh
print('PASS Settings diagnostic replay: v224 base, v225-v236 library, v237, v238 candidate', sha(candidate))
