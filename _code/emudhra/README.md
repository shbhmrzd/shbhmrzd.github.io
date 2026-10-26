# Code behind "eMudhra: A CA Private Key Was Just 156"

Post: `_posts/2026-10-23-emudhra-weak-ca-keys.md`. Jekyll ignores this `_code/` folder, so none of it is published.

The post also needs `_includes/https-walkthrough.html` (the click-through) and `assets/img/emudhra/` (the two curve images).
Commit all of them together.

## scripts/ (produce every output shown in the post)

- `recover.py`: tries d = 1 to 255 against a certificate's public key, then signs and verifies a test message.
  `python3 _code/emudhra/scripts/recover.py _code/emudhra/data/emudhra-g3b.crt` gives `d = 156`.
- `verify_chain.py`: checks the blog's chain signatures by hand and runs the `github.io` to `evilxx.io` tamper test.
  `python3 _code/emudhra/scripts/verify_chain.py _code/emudhra/data/shbhmrzd-github-io-chain-2026-10-23.txt`
- `toy-ca.sh`: builds the toy root, a normal intermediate and a d = 156 intermediate, prints both intermediate private keys, and
  verifies both chains. Run it in an empty folder. The random key is different on every run.
  It needs Homebrew OpenSSL 3+ at `/opt/homebrew/bin/openssl` (the macOS `openssl` is LibreSSL).
- `diagrams.py`: regenerates the two curve PNGs. `python3 _code/emudhra/scripts/diagrams.py assets/img/emudhra`

All the Python needs the `cryptography` package; `diagrams.py` also needs matplotlib and numpy.

## data/

- `emudhra-g3b.crt`: the public `EM DV TLS CA - G3B` certificate from crt.sh (id 18286207191).
- `shbhmrzd-github-io-chain-2026-10-23.txt`: the chain shbhmrzd.github.io sent on 23 Oct 2026, from `curl -w '%{certs}'`.
