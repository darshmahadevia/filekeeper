# Resume and interview notes

Use these bullets after you understand the code, run it yourself, and make a contribution you can explain. Adapt the verbs to your own work. Do not claim deployments, users, measured speedups, or ownership of work you cannot discuss.

**FileKeeper | Python, SHA-256, unittest, GitHub Actions**

- Built a Python CLI to detect duplicate files using size-based filtering and chunked SHA-256 hashing, with text and JSON reports.
- Implemented reversible file quarantine with recovery manifests, content revalidation, and restore conflict checks.
- Added 14 automated tests covering duplicate detection, changed files, symlinks, hard links, path traversal, and interrupted cleanup.

## A short interview introduction

"FileKeeper finds identical files even when their names differ. It first groups by size to avoid hashing files that cannot be duplicates, then hashes candidates in chunks. Cleanup moves extra copies into a quarantine with a recovery manifest. I can restore them without overwriting existing files. The difficult part is dealing with files that change and cleanup that stops midway."

Rewrite that in your own voice and add the extension you built.

## Before applying

Run the demo and tests, complete an extension from the learning guide, and publish the project to a repository you control. Include a short terminal demo in the README. Once GitHub Actions runs, verify its result before describing CI as passing. Add the repository link to your resume.

No performance benchmark has been measured for this version. Describe the algorithm instead of inventing a speedup or storage-savings percentage.
