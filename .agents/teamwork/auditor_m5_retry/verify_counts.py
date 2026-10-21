import unittest
import importlib
import glob
import os

loader = unittest.defaultTestLoader

# Method 1: discover exactly like CLI: python -m unittest discover -s tests -p "test_*.py"
suite_disc = loader.discover(start_dir='tests', pattern='test_*.py', top_level_dir='.')
print(f"CLI-style discover count: {suite_disc.countTestCases()}")

# Method 2: discover file by file
test_files = sorted(glob.glob('tests/**/test_*.py', recursive=True))
print(f"Number of test files found: {len(test_files)}")

file_counts = {}
for tf in test_files:
    clean_path = tf.replace('\\', '/')
    # discover for each file
    dir_name = os.path.dirname(clean_path)
    file_name = os.path.basename(clean_path)
    s = loader.discover(start_dir=dir_name, pattern=file_name, top_level_dir='.')
    cnt = s.countTestCases()
    file_counts[clean_path] = cnt
    print(f"  {clean_path}: {cnt}")

print(f"Sum of file discovers: {sum(file_counts.values())}")
