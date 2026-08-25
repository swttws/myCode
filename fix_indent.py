import sys

path = sys.argv[1]
with open(path, 'r') as f:
    lines = f.readlines()

# Fix indentation: lines 295-299 should have 4-space indent (not 8), line 301-302 should be 4-space
# The issue is that async def scenario(): is at 0 indent, body at 8, then asyncio.run at 4
# We need async def at 0 indent, body at 4, asyncio.run at 4

for i in range(len(lines)):
    stripped = lines[i].rstrip('\n')
    # Line 295 is '        task = ...' with 8 spaces
    if i == 294 and stripped.startswith('        task = asyncio.create_task'):
        lines[i] = '    ' + stripped.lstrip() + '\n'
    elif i == 295 and stripped.startswith('        await started.wait()'):
        lines[i] = '    ' + stripped.lstrip() + '\n'
    elif i == 296 and 'assert store' in stripped:
        lines[i] = '    ' + stripped.lstrip() + '\n'
    elif i == 297 and stripped.startswith('        release.set()'):
        lines[i] = '    ' + stripped.lstrip() + '\n'
    elif i == 298 and stripped.startswith('        await task'):
        lines[i] = '    ' + stripped.lstrip() + '\n'

with open(path, 'w') as f:
    f.writelines(lines)

print('Fixed')