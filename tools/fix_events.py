"""
Quick fix script to add timestamp fields to all event dataclasses.
"""

import re
from pathlib import Path

# Read the file
_repo = Path(__file__).resolve().parent.parent
with open(_repo / 'core' / 'events' / 'types.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Find all dataclass event definitions and add timestamp if missing
# Pattern: @dataclass(frozen=True)\nclass SomeEvent(Event):
pattern = r'(@dataclass\(frozen=True\)\nclass \w+Event\(Event\):.*?(?=\n@dataclass|\n\nclass |\n# =|$))'

def add_timestamp_if_missing(match):
    event_def = match.group(1)
    
    # Skip if already has timestamp
    if 'timestamp: datetime' in event_def:
        return event_def
    
    # Find the last field definition
    lines = event_def.split('\n')
    
    # Find where fields end (before any methods or blank lines)
    field_end_idx = len(lines)
    for i, line in enumerate(lines):
        if line.strip().startswith('def ') or (line.strip() == '' and i > 2):
            field_end_idx = i
            break
    
    # Insert timestamp before field_end
    timestamp_line = '    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))'
    lines.insert(field_end_idx, timestamp_line)
    
    return '\n'.join(lines)

# Apply fix
content_fixed = re.sub(pattern, add_timestamp_if_missing, content, flags=re.DOTALL)

# Write back
with open(_repo / 'core' / 'events' / 'types.py', 'w', encoding='utf-8') as f:
    f.write(content_fixed)

print("Fixed all event classes with timestamp fields")
