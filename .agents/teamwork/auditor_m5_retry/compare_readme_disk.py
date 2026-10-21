import re
import unittest
import os
import glob

# 1. Parse README.md Section 9 table
with open('README.md', 'r', encoding='utf-8') as f:
    readme_content = f.read()

# Find the table in Section 9
table_match = re.search(r'## 9\. Verification & Test Coverage Summary.*?\n(\| :---.*?\n)(.*?)\n\n---', readme_content, re.DOTALL)
if not table_match:
    print("FAILED TO MATCH TABLE IN README.MD!")
    exit(1)

table_rows = table_match.group(2).strip().split('\n')
readme_entries = {}
readme_total = None

for row in table_rows:
    parts = [p.strip() for p in row.split('|')[1:-1]]
    if len(parts) >= 3:
        file_col = parts[0]
        count_col = parts[2]
        if 'Repository Total' in file_col:
            readme_total = int(count_col.replace('*', '').strip())
        else:
            # extract path
            m = re.search(r'`(.*?)`', file_col)
            if m:
                path = m.group(1)
                count = int(count_col.replace('*', '').strip())
                readme_entries[path] = count

print(f"Parsed {len(readme_entries)} entries from README.md. Total claimed in row: {readme_total}")

# 2. Discover test files on disk
loader = unittest.defaultTestLoader
disk_files = sorted([f.replace('\\', '/') for f in glob.glob('tests/**/test_*.py', recursive=True)])
print(f"Found {len(disk_files)} test files on disk.")

disk_counts = {}
for df in disk_files:
    dir_name = os.path.dirname(df)
    file_name = os.path.basename(df)
    suite = loader.discover(start_dir=dir_name, pattern=file_name, top_level_dir='.')
    cnt = suite.countTestCases()
    disk_counts[df] = cnt

# 3. Compare
all_clean = True
print("\n--- LINE-BY-LINE COMPARISON ---")
for path in sorted(set(list(readme_entries.keys()) + list(disk_counts.keys()))):
    in_readme = path in readme_entries
    in_disk = path in disk_counts
    r_cnt = readme_entries.get(path, 'MISSING_IN_README')
    d_cnt = disk_counts.get(path, 'MISSING_ON_DISK')
    
    match = (r_cnt == d_cnt)
    if not match:
        all_clean = False
    print(f"File: {path:<50} | README: {str(r_cnt):<5} | Disk: {str(d_cnt):<5} | Match: {match}")

print("\n--- TOTALS ---")
sum_readme = sum(readme_entries.values())
sum_disk = sum(disk_counts.values())
print(f"Sum of README table entries: {sum_readme}")
print(f"Claimed Repository Total row: {readme_total}")
print(f"Sum of Disk test counts:    {sum_disk}")

if all_clean and sum_readme == 443 and sum_disk == 443 and readme_total == 443 and len(readme_entries) == 19:
    print("\nPERFECT MATCH: All 19 files, counts, and sums match 443!")
else:
    print("\nDISCREPANCY DETECTED!")
