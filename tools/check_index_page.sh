#!/bin/sh
# Execute public/index.html's script against the published dashboard.json
# with a stub DOM — catches runtime errors node --check cannot (wrong-scope
# insertions, TDZ, missing elements).
cd "$(dirname "$0")"
python3 -c "
import re; s=open('../public/index.html').read()
open('index_page.extracted.js','w').write(re.search(r'<script>(.*)</script>', s, re.S).group(1))"
node index_page_harness.js
rm -f index_page.extracted.js
