#!/bin/bash
set -e

# 1. Update index.html references using python
python3 -c "
path = '/home/laophan/clipforge-work/web/src/index.html'
with open(path, 'r') as f:
    content = f.read()
content = content.replace('href=\"style.css?v=5\"', 'href=\"./style.css\"')
content = content.replace('<script src=\"app.js?v=5\"></script>', '<script type=\"module\" src=\"./main.ts\"></script>')
with open(path, 'w') as f:
    f.write(content)
print('index.html updated!')
"

# 2. Update remote URL with new PAT
git -C /home/laophan/clipforge-work remote set-url origin https://RituRajDeka:github_pat_11CL3H4TYgGtrm31NL0Fm_0AvfpmMunvFyC2sWxXpEvO15fmSOQ1qZN8QYAPyKnjxQGYBRQKDcXvqMBGw@github.com/RituRajDeka/YourAutoHelper.git

# 3. Build React App
cd /home/laophan/clipforge-work/web
npm install
npm run build

# 4. Force add compiled assets and commit/push
cd /home/laophan/clipforge-work
git add -f web/dist
git commit -m "Add compiled frontend assets" || echo "No changes to commit"
git push origin main
