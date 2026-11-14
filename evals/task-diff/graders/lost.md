---
type: regex
pattern: '"after": \{[^}]*"verdict": "CCBot \S*/ => deny @L7"'
target: {source: file, path: crawlpolicy-report.json}
---
